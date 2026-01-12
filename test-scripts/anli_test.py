from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import random
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import pymysql  # type: ignore

DEFAULT_DB = {
    "host": "172.16.15.255",
    "database": "ft",
    "user": "root",
    # "host": "freetier-01.cn-hangzhou.cluster.cn-dev.matrixone.tech",
    # "database": "test",
    # "user": "snapshot_test_4eqx53:admin",
    "port": 6001,
    "password": "111",
}

DEFAULT_TABLE = "ca_comprehensive_dataset"

SQL1_TEMPLATE = """
WITH t AS (
  SELECT
    md5_id,
    question,
    answer,
    content_type,
    source_type,
    l2_distance(question_vector, %s) AS vec_dist,
    allow_access,
    allow_identities,
    delete_flag
  FROM
    {table}
  WHERE
    question_vector IS NOT NULL AND
    delete_flag IS NOT NULL
)
SELECT
  md5_id,
  question,
  answer,
  source_type,
  vec_dist
FROM
  t
WHERE
  vec_dist<0.96 AND
  (
    LOCATE('OPEN', allow_access) > 0
    OR (
      LOCATE('ABO', allow_access) > 0
      AND LOCATE('SA_O', allow_identities) > 0
    )
  )
  AND LOCATE('日更', content_type) = 0
  AND LOCATE('直播', content_type) = 0
ORDER BY
  vec_dist
LIMIT 30
"""

SQL2_TEMPLATE = """
WITH t AS (
  SELECT
      md5_id,
      question,
      answer,
      keyword,
      content_type,
      source_type,
      MATCH (question) AGAINST (%s IN BOOLEAN MODE) AS vec_dist,
      allow_access,
      allow_identities
  FROM
      {table}
  WHERE
      MATCH (question) AGAINST (%s IN BOOLEAN MODE)
      AND delete_flag IS NULL
)
SELECT md5_id, question, answer, source_type, vec_dist
FROM t
WHERE (
        locate('OPEN', allow_access) > 0
        OR (locate('ABO', allow_access) > 0 AND locate('SA_O', allow_identities) > 0)
      )
      AND locate('日更', content_type) = 0
      AND locate('直播', content_type) = 0
LIMIT 30
"""

NQVEC_CANDIDATE_TEMPLATE = """
SELECT
  md5_id,
  l2_distance(question_vector, %s) AS vec_dist
FROM
  {table}
ORDER BY vec_dist
LIMIT %s
"""

NQVEC_RESULT_TEMPLATE = """
SELECT
  md5_id,
  question,
  answer,
  source_type
FROM {table}
WHERE
  md5_id IN ({placeholders})
  AND (
        locate('OPEN', allow_access) > 0
        OR (locate('ABO', allow_access) > 0 AND locate('SA_O', allow_identities) > 0)
      )
  AND locate('日更', content_type) = 0
  AND locate('直播', content_type) = 0
LIMIT 30
"""

NQFT_CANDIDATE_TEMPLATE = """
SELECT
  md5_id,
  MATCH (question) AGAINST (%s IN BOOLEAN MODE) AS vec_dist
FROM
  {table}
LIMIT %s
"""

NQFT_RESULT_TEMPLATE = """
SELECT
  md5_id,
  question,
  answer
FROM {table}
WHERE
  md5_id IN ({placeholders})
  AND (
        locate('OPEN', allow_access) > 0
        OR (locate('ABO', allow_access) > 0 AND locate('SA_O', allow_identities) > 0)
      )
  AND locate('日更', content_type) = 0
  AND locate('直播', content_type) = 0
ORDER BY FIELD(md5_id, {placeholders})
LIMIT 30
"""

NQFT_CANDIDATE_LIMIT = 200
NQVEC_CANDIDATE_LIMIT = 100


def percentile(values: List[float], ratio: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    rank = (len(ordered) - 1) * ratio
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


class Metrics:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.total = 0
        self.success = 0
        self.failure = 0
        self.latencies: List[float] = []
        self.errors: Counter[str] = Counter()
        self.first_request_time: float | None = None

    def mark_start(self) -> None:
        with self.lock:
            if self.first_request_time is None:
                self.first_request_time = time.perf_counter()

    def record(self, success: bool, latency_ms: float, error: str | None = None) -> None:
        with self.lock:
            self.total += 1
            if success:
                self.success += 1
                self.latencies.append(latency_ms)
            else:
                self.failure += 1
                if error:
                    self.errors[error] += 1

    def snapshot(self) -> dict:
        with self.lock:
            latencies_copy = list(self.latencies)
            max_latency = max(latencies_copy) if latencies_copy else None
            min_latency = min(latencies_copy) if latencies_copy else None
            errors_copy = self.errors.most_common(5)
            elapsed = (
                time.perf_counter() - self.first_request_time if self.first_request_time is not None else None
            )
            qpm = (self.total / (elapsed / 60)) if elapsed and elapsed > 0 else None
            success_qpm = (self.success / (elapsed / 60)) if elapsed and elapsed > 0 else None
            qps = (self.total / elapsed) if elapsed and elapsed > 0 else None
            success_qps = (self.success / elapsed) if elapsed and elapsed > 0 else None
            # 理论并发度 = QPS * 平均延迟（秒）
            theoretical_concurrency = (
                (qps * (sum(latencies_copy) / 1000 / len(latencies_copy)))
                if qps and latencies_copy
                else None
            )
            data = {
                "total": self.total,
                "success": self.success,
                "failure": self.failure,
                "avg_latency_ms": (sum(latencies_copy) / len(latencies_copy)) if latencies_copy else None,
                "p95_latency_ms": percentile(latencies_copy, 0.95),
                "p99_latency_ms": percentile(latencies_copy, 0.99),
                "p50_latency_ms": percentile(latencies_copy, 0.5),
                "max_latency_ms": max_latency,
                "min_latency_ms": min_latency,
                "qpm": qpm,
                "success_qpm": success_qpm,
                "qps": qps,
                "success_qps": success_qps,
                "theoretical_concurrency": theoretical_concurrency,
                "elapsed_seconds": elapsed,
                "errors": errors_copy,
            }
        return data


def format_latency_summary(snapshot: dict) -> str:
    def format_ms(value: float | None) -> str:
        return f"{value:.1f}ms" if value is not None else "n/a"

    def format_qpm(value: float | None) -> str:
        return f"{value:.1f}/m" if value is not None else "n/a"

    def format_qps(value: float | None) -> str:
        return f"{value:.1f}/s" if value is not None else "n/a"

    def format_concurrency(value: float | None) -> str:
        return f"{value:.1f}" if value is not None else "n/a"

    return (
        f"total={snapshot['total']} success={snapshot['success']} failure={snapshot['failure']} "
        f"avg={format_ms(snapshot['avg_latency_ms'])} "
        f"p50={format_ms(snapshot['p50_latency_ms'])} "
        f"p95={format_ms(snapshot['p95_latency_ms'])} "
        f"p99={format_ms(snapshot['p99_latency_ms'])} "
        f"min={format_ms(snapshot['min_latency_ms'])} "
        f"max={format_ms(snapshot['max_latency_ms'])} "
        f"qps={format_qps(snapshot.get('qps'))} "
        f"qpm={format_qpm(snapshot.get('qpm'))} "
        f"concurrency={format_concurrency(snapshot.get('theoretical_concurrency'))}"
    )


def normalize_vector(raw_value: object) -> List[float]:
    """
    将数据库返回的向量值统一转换为 float list。
    """
    if raw_value is None:
        raise ValueError("向量值为空")

    if isinstance(raw_value, (list, tuple)):
        return [float(x) for x in raw_value]

    if isinstance(raw_value, (bytes, bytearray)):
        raw_value = raw_value.decode("utf-8")

    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"无法解析向量字符串: {raw_value[:60]}...") from exc
        if isinstance(parsed, list):
            return [float(x) for x in parsed]
        raise ValueError(f"向量解析结果不是列表: {type(parsed)}")

    raise TypeError(f"不支持的向量类型: {type(raw_value)}")


def tokenize_text(
        text: str | None,
        *,
        mode: str,
        min_length: int,
) -> List[str]:
    """
    使用结巴分词对 question 文本进行分词，返回过滤后的 token 列表。
    """
    import jieba  # type: ignore

    if not text:
        return []

    text = text.strip()
    if not text:
        return []

    if mode == "full":
        tokens = jieba.lcut(text, cut_all=True)
    elif mode == "search":
        tokens = jieba.lcut_for_search(text)
    else:
        tokens = jieba.lcut(text, cut_all=False)

    min_len = max(1, min_length)
    return [token.strip() for token in tokens if token.strip() and len(token.strip()) >= min_len]


def chunk_random_tokens(
        tokens: List[str],
        min_size: int,
        max_size: int,
) -> Iterable[List[str]]:
    """
    将一个 token 列表随机打散为多行，长度范围 [min_size, max_size]。
    """
    if not tokens:
        return []

    size_min = max(1, min_size)
    size_max = max(size_min, max_size)

    pool = tokens[:]
    random.shuffle(pool)

    idx = 0
    total = len(pool)
    chunks: List[List[str]] = []
    while idx < total:
        remaining = total - idx
        chunk_size = random.randint(size_min, size_max)
        if chunk_size > remaining:
            chunk_size = remaining
        chunk = pool[idx: idx + chunk_size]
        chunks.append(chunk)
        idx += chunk_size
    return chunks


def add_db_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default=DEFAULT_DB["host"])
    parser.add_argument("--port", type=int, default=DEFAULT_DB["port"])
    parser.add_argument("--user", default=DEFAULT_DB["user"])
    parser.add_argument("--password", default=DEFAULT_DB["password"])
    parser.add_argument("--database", default=DEFAULT_DB["database"])


def chunked(iterable: Sequence, size: int) -> Iterable[Sequence]:
    for idx in range(0, len(iterable), size):
        yield iterable[idx: idx + size]


def load_vectors_file(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"未找到向量文件: {path}")
    vector_literals: List[str] = []
    with path.open("r", encoding="utf-8") as infile:
        for line in infile:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"解析向量文件失败: {line[:60]}...") from exc
            if isinstance(record, dict):
                vector = record.get("vector") or record.get("question_vector")
            else:
                vector = record
            vector_list = normalize_vector(vector)
            vector_literals.append(json.dumps(vector_list, ensure_ascii=False))
    if not vector_literals:
        raise ValueError(f"向量文件 {path} 无可用数据")
    return vector_literals


def load_tokens_file(path: Path) -> List[List[str]]:
    if not path.exists():
        raise FileNotFoundError(f"未找到 tokens 文件: {path}")
    token_sets: List[List[str]] = []
    with path.open("r", encoding="utf-8") as infile:
        for line in infile:
            line = line.strip()
            if not line:
                continue
            try:
                tokens = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"解析 tokens 行失败: {line[:60]}...") from exc
            if not isinstance(tokens, list):
                raise ValueError(f"tokens 行不是列表: {line}")
            cleaned = [str(token).strip() for token in tokens if str(token).strip()]
            if cleaned:
                token_sets.append(cleaned)
    if not token_sets:
        raise ValueError(f"tokens 文件 {path} 无可用数据")
    return token_sets


def download_vectors(
        *,
        count: int,
        batch_size: int,
        output: Path,
        progress_interval: int,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        table: str,
) -> None:
    """
    从数据库下载指定数量的 question_vector，并写入 JSON Lines 文件。
    先获取所有符合条件的 md5_id，然后随机选择指定数量，再查询对应的 vector。
    """
    print("=" * 80, file=sys.stderr)
    print("下载向量参数", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    print(f"[数据库配置]", file=sys.stderr)
    print(f"  主机: {host}:{port}", file=sys.stderr)
    print(f"  数据库: {database}", file=sys.stderr)
    print(f"  用户: {user}", file=sys.stderr)
    print(f"  表: {table}", file=sys.stderr)
    print(f"\n[下载配置]", file=sys.stderr)
    print(f"  目标数量: {count}", file=sys.stderr)
    print(f"  批次大小: {batch_size}", file=sys.stderr)
    print(f"  进度间隔: {progress_interval}", file=sys.stderr)
    print(f"  输出文件: {output}", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    print("", file=sys.stderr, flush=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    start_time = time.perf_counter()

    connection = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )

    try:
        with connection.cursor() as cursor:
            # 第一步：获取所有符合条件的 md5_id
            print("[vectors] 正在获取所有 md5_id...", file=sys.stderr, flush=True)
            sql_get_ids = f"""
                SELECT md5_id
                FROM {table}
                WHERE question_vector IS NOT NULL
                  AND delete_flag IS NULL
            """
            cursor.execute(sql_get_ids)
            all_ids = [row["md5_id"] for row in cursor.fetchall()]
            total_available = len(all_ids)
            print(
                f"[vectors] 共找到 {total_available} 条符合条件的记录",
                file=sys.stderr,
                flush=True,
            )

            if total_available == 0:
                print("[vectors] 没有找到符合条件的记录", file=sys.stderr)
                return

            # 第二步：随机选择指定数量的 id
            selected_count = min(count, total_available)
            selected_ids = random.sample(all_ids, selected_count)
            print(
                f"[vectors] 随机选择了 {selected_count} 个 md5_id",
                file=sys.stderr,
                flush=True,
            )

            # 第三步：使用选中的 id 批量查询对应的 vector
            with output.open("w", encoding="utf-8") as outfile:
                downloaded = 0
                # 分批处理，避免 IN 子句过长
                for i in range(0, len(selected_ids), batch_size):
                    batch_ids = selected_ids[i: i + batch_size]
                    placeholders = ",".join(["%s"] * len(batch_ids))
                    sql_get_vectors = f"""
                        SELECT md5_id, question_vector
                        FROM {table}
                        WHERE md5_id IN ({placeholders})
                    """
                    cursor.execute(sql_get_vectors, batch_ids)
                    rows = cursor.fetchall()

                    for row in rows:
                        vector = normalize_vector(row["question_vector"])
                        payload = {
                            "md5_id": row["md5_id"],
                            "vector": vector,
                        }
                        json.dump(payload, outfile, ensure_ascii=False)
                        outfile.write("\n")
                        downloaded += 1

                    if downloaded % progress_interval == 0 or downloaded == selected_count:
                        elapsed = time.perf_counter() - start_time
                        print(
                            f"[vectors] 已下载 {downloaded}/{selected_count} 条，用时 {elapsed:.1f}s",
                            file=sys.stderr,
                            flush=True,
                        )

                if downloaded < count:
                    print(
                        f"[vectors] 仅获取到 {downloaded} 条（目标 {count}）；数据可能不足。",
                        file=sys.stderr,
                    )
                else:
                    total_time = time.perf_counter() - start_time
                    print(
                        f"[vectors] 完成下载 {downloaded} 条，耗时 {total_time:.1f}s，输出文件 {output}",
                        file=sys.stderr,
                    )

    finally:
        connection.close()


def download_tokens(
        *,
        count: int,
        batch_size: int,
        offset: int,
        randomize: bool,
        min_line_size: int,
        max_line_size: int,
        tokenizer_mode: str,
        min_token_length: int,
        output: Path,
        progress_interval: int,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        table: str,
) -> None:
    """
    下载指定数量的查询 tokens，并写入文本文件，每行一个 JSON 数组。
    """
    print("=" * 80, file=sys.stderr)
    print("下载Tokens参数", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    print(f"[数据库配置]", file=sys.stderr)
    print(f"  主机: {host}:{port}", file=sys.stderr)
    print(f"  数据库: {database}", file=sys.stderr)
    print(f"  用户: {user}", file=sys.stderr)
    print(f"\n[下载配置]", file=sys.stderr)
    print(f"  目标数量: {count}", file=sys.stderr)
    print(f"  批次大小: {batch_size}", file=sys.stderr)
    print(f"  偏移量: {offset}", file=sys.stderr)
    print(f"  随机采样: {'是' if randomize else '否'}", file=sys.stderr)
    print(f"  进度间隔: {progress_interval}", file=sys.stderr)
    print(f"\n[分词配置]", file=sys.stderr)
    print(f"  分词模式: {tokenizer_mode}", file=sys.stderr)
    print(f"  最小token长度: {min_token_length}", file=sys.stderr)
    print(f"  每行token数: {min_line_size}-{max_line_size}", file=sys.stderr)
    print(f"\n[输出配置]", file=sys.stderr)
    print(f"  输出文件: {output}", file=sys.stderr)
    print(f"  数据表: {table}", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    print("", file=sys.stderr, flush=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    processed_questions = 0
    written_lines = 0
    start_time = time.perf_counter()

    connection = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.SSDictCursor,
    )

    try:
        with connection.cursor() as cursor, output.open("w", encoding="utf-8") as outfile:
            base_sql = f"""
                SELECT question
                FROM {table}
                WHERE question IS NOT NULL
                  AND delete_flag IS NULL
            """
            if randomize:
                sql = base_sql + " ORDER BY RAND() LIMIT %s"
                cursor.execute(sql, (count,))
            else:
                sql = base_sql + " LIMIT %s OFFSET %s"
                cursor.execute(sql, (count, offset))

            while processed_questions < count:
                rows = cursor.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    tokens = tokenize_text(
                        row["question"],
                        mode=tokenizer_mode,
                        min_length=min_token_length,
                    )
                    if not tokens:
                        continue
                    chunks = list(
                        chunk_random_tokens(
                            tokens,
                            min_line_size,
                            max_line_size,
                        )
                    )
                    for chunk in chunks:
                        if not chunk:
                            continue
                        json.dump(chunk, outfile, ensure_ascii=False)
                        outfile.write("\n")
                        written_lines += 1

                    processed_questions += 1
                    if processed_questions >= count:
                        break

                if processed_questions and (
                        processed_questions % progress_interval == 0 or processed_questions == count
                ):
                    elapsed = time.perf_counter() - start_time
                    print(
                        f"[tokens] 已处理 {processed_questions}/{count} 个问题，生成 {written_lines} 行，用时 {elapsed:.1f}s",
                        file=sys.stderr,
                        flush=True,
                    )
    finally:
        connection.close()

    if processed_questions < count:
        print(
            f"[tokens] 仅处理 {processed_questions} 个问题（目标 {count}）；数据可能不足。",
            file=sys.stderr,
        )
    else:
        total_time = time.perf_counter() - start_time
        print(
            f"[tokens] 完成处理 {processed_questions} 个问题，生成 {written_lines} 行，耗时 {total_time:.1f}s，输出文件 {output}",
            file=sys.stderr,
        )


def run_load_test(
        *,
        query_type: str,
        vectors_file: Path | None,
        tokens_file: Path | None,
        concurrency: int,
        type_concurrency: int,
        mixed_types: List[str],
        duration: int,
        progress_interval: int,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        connect_timeout: float,
        read_timeout: float,
        verbose: bool,
        tables: Sequence[str],
) -> None:
    tables = [tbl.strip() for tbl in tables if tbl and tbl.strip()]
    if not tables:
        raise ValueError("需要至少指定一张可用的数据表")
    # 打印运行参数
    import datetime
    print("=" * 80, file=sys.stderr)
    print("压测运行参数", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    print(f"[环境信息]", file=sys.stderr)
    print(f"  开始时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", file=sys.stderr)
    print(f"  Python版本: {sys.version.split()[0]}", file=sys.stderr)
    print(f"  PyMySQL版本: {pymysql.__version__}", file=sys.stderr)
    print(f"\n[数据库配置]", file=sys.stderr)
    print(f"  主机: {host}:{port}", file=sys.stderr)
    print(f"  数据库: {database}", file=sys.stderr)
    print(f"  用户: {user}", file=sys.stderr)
    print(f"  密码: {'*' * len(password) if password else '(empty)'}", file=sys.stderr)
    print(f"  表: {', '.join(tables)}", file=sys.stderr)
    print(f"\n[查询配置]", file=sys.stderr)
    print(f"  查询类型: {query_type}", file=sys.stderr)

    allowed_types = {"qvec", "nqvec", "qft", "nqft"}
    if query_type == "mixed":
        active_types = [kind.strip() for kind in mixed_types if kind.strip()]
        if not active_types:
            raise ValueError("混合模式需要至少指定一种查询类型")
        for kind in active_types:
            if kind not in allowed_types:
                raise ValueError(f"不支持的混合类型: {kind}")
    else:
        if query_type not in allowed_types:
            raise ValueError(f"未知 query_type: {query_type}")
        active_types = [query_type]

    print(f"  激活类型: {', '.join(active_types)}", file=sys.stderr)

    # 打印查询特定配置
    if "nqvec" in active_types:
        print(f"  nqvec候选数限制: {NQVEC_CANDIDATE_LIMIT}", file=sys.stderr)
    if "nqft" in active_types:
        print(f"  nqft候选数限制: {NQFT_CANDIDATE_LIMIT}", file=sys.stderr)

    needs_vector = any(kind in {"qvec", "nqvec"} for kind in active_types)
    needs_tokens = any(kind in {"qft", "nqft"} for kind in active_types)

    print(f"  详细SQL日志: {'是' if verbose else '否'}", file=sys.stderr)

    print(f"\n[并发配置]", file=sys.stderr)
    if query_type == "mixed":
        print(f"  模式: 混合模式", file=sys.stderr)
        print(f"  每类型并发数: {type_concurrency}", file=sys.stderr)
        print(f"  总并发数: {type_concurrency * len(active_types)}", file=sys.stderr)
    else:
        print(f"  模式: 单类型", file=sys.stderr)
        print(f"  并发线程数: {concurrency}", file=sys.stderr)

    print(f"\n[测试配置]", file=sys.stderr)
    print(f"  测试时长: {duration}s", file=sys.stderr)
    print(f"  进度间隔: {progress_interval}s", file=sys.stderr)

    print(f"\n[连接管理]", file=sys.stderr)
    print(f"  连接超时: {connect_timeout}s", file=sys.stderr)
    print(f"  读写超时: {read_timeout}s", file=sys.stderr)
    print(f"  连接重试阈值: 10次连续失败后自动重建", file=sys.stderr)
    print(f"  连接错误处理: 检测到连接错误立即重建", file=sys.stderr)

    print(f"\n[数据文件]", file=sys.stderr)

    vector_literals: List[str] = []
    token_sets: List[List[str]] = []
    if needs_vector:
        if vectors_file is None:
            raise ValueError("需要 --vectors-file")
        print(f"  向量文件: {vectors_file}", file=sys.stderr)
        vector_literals = load_vectors_file(vectors_file)
        print(f"  已加载向量: {len(vector_literals)} 条", file=sys.stderr)
    if needs_tokens:
        if tokens_file is None:
            raise ValueError("需要 --tokens-file")
        print(f"  Tokens文件: {tokens_file}", file=sys.stderr)
        token_sets = load_tokens_file(tokens_file)
        print(f"  已加载tokens: {len(token_sets)} 行", file=sys.stderr)

    if not needs_vector and not needs_tokens:
        print(f"  (无需数据文件)", file=sys.stderr)

    print("=" * 80, file=sys.stderr)
    print("", file=sys.stderr, flush=True)

    stop_time = time.perf_counter() + duration
    metrics_by_kind = {kind: Metrics() for kind in active_types}
    metrics_overall = None if len(active_types) > 1 else metrics_by_kind[active_types[0]]
    stop_event = threading.Event()

    def make_connection():
        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            write_timeout=read_timeout,
            autocommit=True, # 确保每个语句自动提交，避免多个语句累积在同一事务中
        )
        return conn

    def mask_vectors(text: str, params: Tuple) -> str:
        for value in params:
            if isinstance(value, str):
                stripped = value.strip()
                if (stripped.startswith("[") and stripped.endswith("]")) or len(stripped) > 200:
                    text = text.replace(value, "...")
        return text

    def execute_with_log(cursor, sql: str, params: Tuple, kind: str):
        if verbose:
            try:
                rendered = cursor.mogrify(sql, params)
                text = rendered.decode("utf-8") if isinstance(rendered, (bytes, bytearray)) else str(rendered)
                text = mask_vectors(text, params)
            except Exception:
                text = f"{sql.strip()} || params={params}"
            print(f"[sql][{kind}] {text}", flush=True)
        cursor.execute(sql, params)
        return cursor.fetchall()

    def execute_query(connection, kind: str, table: str) -> None:
        if kind == "qvec":
            vector_literal = random.choice(vector_literals)
            params = (vector_literal,)
            sql = SQL1_TEMPLATE.format(table=table)
            with connection.cursor() as cursor:
                execute_with_log(cursor, sql, params, f"{kind}@{table}")
            return
        elif kind == "nqvec":
            vector_literal = random.choice(vector_literals)
            with connection.cursor() as cursor:
                candidates = execute_with_log(
                    cursor,
                    NQVEC_CANDIDATE_TEMPLATE.format(table=table),
                    (vector_literal, NQVEC_CANDIDATE_LIMIT),
                    f"{kind}-candidate@{table}",
                )
            md5_ids = [row["md5_id"] for row in candidates if row.get("md5_id")]
            if not md5_ids:
                raise ValueError("nqvec 未获取到有效 md5_id")
            placeholders = ",".join(["%s"] * len(md5_ids))
            sql = NQVEC_RESULT_TEMPLATE.format(placeholders=placeholders, table=table)
            params = tuple(md5_ids)
            with connection.cursor() as cursor:
                execute_with_log(cursor, sql, params, f"{kind}@{table}")
            return
        elif kind == "qft":
            tokens = random.choice(token_sets)
            # 过滤掉只包含标点/符号的 token，防止拼出无效的搜索串
            valid_tokens = [t for t in tokens if re.search(r'[a-zA-Z0-9\u4e00-\u9fff]', t)]
            query_string = " ".join(valid_tokens).strip()
            if not query_string:
                return  # Skip empty search pattern
            params = (query_string, query_string)
            sql = SQL2_TEMPLATE.format(table=table)
            with connection.cursor() as cursor:
                execute_with_log(cursor, sql, params, f"{kind}@{table}")
            return
        elif kind == "nqft":
            tokens = random.choice(token_sets)
            # 过滤掉只包含标点/符号的 token，防止拼出无效的搜索串
            valid_tokens = [t for t in tokens if re.search(r'[a-zA-Z0-9\u4e00-\u9fff]', t)]
            query_string = " ".join(valid_tokens).strip()
            if not query_string:
                return  # Skip empty search pattern
            with connection.cursor() as cursor:
                candidates = execute_with_log(
                    cursor,
                    NQFT_CANDIDATE_TEMPLATE.format(table=table),
                    (query_string, NQFT_CANDIDATE_LIMIT),
                    f"{kind}-candidate@{table}",
                )
            md5_ids = [row["md5_id"] for row in candidates if row.get("md5_id")]
            if not md5_ids:
                raise ValueError("nqft 未获取到有效 md5_id")
            placeholders = ",".join(["%s"] * len(md5_ids))
            sql = NQFT_RESULT_TEMPLATE.format(placeholders=placeholders, table=table)
            # placeholders appear twice: once in IN (...) and once in ORDER BY FIELD(...),
            # so we need to pass parameters twice.
            params = tuple(md5_ids) + tuple(md5_ids)
            with connection.cursor() as cursor:
                execute_with_log(cursor, sql, params, f"{kind}@{table}")
            return
        else:
            raise ValueError(f"未知查询类型: {kind}")

    def worker(thread_id: int, kind: str) -> None:
        table = tables[thread_id % len(tables)]
        if kind in {"qvec", "nqvec"} and not vector_literals:
            raise ValueError(f"{kind} worker 需要向量样本文件")
        if kind in {"qft", "nqft"} and not token_sets:
            raise ValueError(f"{kind} worker 需要 tokens 样本文件")

        connection = None
        consecutive_failures = 0
        max_consecutive_failures = 10

        try:
            while not stop_event.is_set():
                now = time.perf_counter()
                if now >= stop_time:
                    break

                # 如果连接不存在或有太多连续失败,重新创建连接
                if connection is None or consecutive_failures >= max_consecutive_failures:
                    if connection is not None:
                        try:
                            connection.close()
                        except Exception:  # pylint: disable=broad-except
                            pass
                    try:
                        connection = make_connection()
                        if consecutive_failures >= max_consecutive_failures:
                            consecutive_failures = 0
                    except Exception as exc:  # pylint: disable=broad-except
                        error_msg = f"{type(exc).__name__}: {exc}"
                        metrics_by_kind[kind].record(False, 0, error=f"连接失败: {error_msg}")
                        time.sleep(1)  # 连接失败时短暂休眠
                        continue

                metrics_by_kind[kind].mark_start()
                start = time.perf_counter()
                try:
                    execute_query(connection, kind, table)
                    latency_ms = (time.perf_counter() - start) * 1000
                    metrics_by_kind[kind].record(True, latency_ms)
                    consecutive_failures = 0  # 成功后重置失败计数
                except Exception as exc:  # pylint: disable=broad-except
                    latency_ms = (time.perf_counter() - start) * 1000
                    consecutive_failures += 1

                    # 格式化错误信息,包含异常类型
                    error_type = type(exc).__name__
                    if hasattr(exc, 'args') and exc.args:
                        error_msg = f"({error_type}, {exc.args})"
                    else:
                        error_msg = f"({error_type}, {str(exc)})"

                    metrics_by_kind[kind].record(False, latency_ms, error=error_msg)

                    # 检查是否是连接相关错误,如果是则立即重建连接
                    is_connection_error = (
                            isinstance(exc, (pymysql.err.OperationalError, pymysql.err.InterfaceError))
                            or 'connection' in str(exc).lower()
                            or 'lost' in str(exc).lower()
                    )

                    if is_connection_error:
                        try:
                            connection.close()
                        except Exception:  # pylint: disable=broad-except
                            pass
                        connection = None  # 标记需要重建连接
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:  # pylint: disable=broad-except
                    pass

    if len(active_types) > 1:
        per_type_workers = max(1, type_concurrency)
        total_threads = per_type_workers * len(active_types)
    else:
        # 单类型模式：如果指定了 type_concurrency 就使用它，否则使用 concurrency
        per_type_workers = max(1, type_concurrency if query_type == "mixed" else concurrency)
        total_threads = per_type_workers

    print(
        f"[info] 启动 {total_threads} 个 worker 线程（{'混合模式' if len(active_types) > 1 else '单模式'}）",
        file=sys.stderr,
        flush=True,
    )
    if len(active_types) > 1:
        print(
            f"[info] 每种查询类型 {per_type_workers} 个线程: {', '.join(active_types)}",
            file=sys.stderr,
            flush=True,
        )
    else:
        kind = active_types[0]
        print(
            f"[info] {kind} 查询使用 {per_type_workers} 个线程",
            file=sys.stderr,
            flush=True,
        )
    with concurrent.futures.ThreadPoolExecutor(max_workers=total_threads) as executor:
        futures: List[concurrent.futures.Future[None]] = []
        current_id = 0
        if len(active_types) > 1:
            for kind in active_types:
                for _ in range(per_type_workers):
                    futures.append(executor.submit(worker, current_id, kind))
                    current_id += 1
        else:
            kind = active_types[0]
            for idx in range(total_threads):
                futures.append(executor.submit(worker, idx, kind))
        try:
            while time.perf_counter() < stop_time:
                time.sleep(progress_interval)
                for kind in active_types:
                    snapshot = metrics_by_kind[kind].snapshot()
                    print(f"[progress][{kind}] {format_latency_summary(snapshot)}", flush=True)
                    if snapshot["errors"]:
                        print(f"  [{kind}] 常见错误: {snapshot['errors']}", flush=True)
        except KeyboardInterrupt:
            print("收到中断信号，准备停止...", file=sys.stderr)
        finally:
            stop_event.set()
            concurrent.futures.wait(futures)

    result: dict[str, dict] = {}
    if metrics_overall is not None:
        result["overall"] = metrics_overall.snapshot()
    for kind, metric in metrics_by_kind.items():
        result[kind] = metric.snapshot()
    print(json.dumps(result, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SQL 压测辅助工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    vector_parser = subparsers.add_parser("download-vectors", help="下载 question_vector 到本地文件")
    add_db_arguments(vector_parser)
    vector_parser.add_argument("--count", type=int, required=True, help="需要下载的向量数量")
    vector_parser.add_argument("--batch-size", type=int, default=128, help="每次 fetchmany 的批量大小")
    vector_parser.add_argument("--progress-interval", type=int, default=500, help="每多少条打印一次进度")
    vector_parser.add_argument(
        "--output",
        type=Path,
        default=Path("vector_samples.jsonl"),
        help="输出 JSON Lines 文件路径",
    )
    vector_parser.add_argument(
        "--table",
        default=DEFAULT_TABLE,
        help="读取向量的表名（默认 ca_comprehensive_dataset）",
    )

    token_parser = subparsers.add_parser("download-tokens", help="下载查询 tokens 到本地文件")
    add_db_arguments(token_parser)
    token_parser.add_argument("--count", type=int, required=True, help="需要下载的 tokens 行数")
    token_parser.add_argument("--batch-size", type=int, default=256, help="每次 fetchmany 的批量大小")
    token_parser.add_argument("--progress-interval", type=int, default=200, help="每多少条打印一次进度")
    token_parser.add_argument("--offset", type=int, default=0, help="查询偏移量，用于跳过前 N 条记录")
    token_parser.add_argument(
        "--randomize",
        action="store_true",
        help="随机采样 question（使用 ORDER BY RAND()，仅适合较小数据量）",
    )
    token_parser.add_argument(
        "--min-line-size",
        type=int,
        default=2,
        help="每行最少 token 数",
    )
    token_parser.add_argument(
        "--max-line-size",
        type=int,
        default=5,
        help="每行最多 token 数",
    )
    token_parser.add_argument(
        "--mode",
        choices=["accurate", "full", "search"],
        default="accurate",
        help="结巴分词模式：accurate(精确)、full(全模式)、search(搜索引擎)",  # noqa: E501
    )
    token_parser.add_argument(
        "--min-token-length",
        type=int,
        default=1,
        help="过滤掉长度小于该值的 token",
    )
    token_parser.add_argument(
        "--output",
        type=Path,
        default=Path("token_samples.jsonl"),
        help="输出 JSON Lines 文件路径",
    )
    token_parser.add_argument(
        "--table",
        default=DEFAULT_TABLE,
        help="读取 question 的表名（默认 ca_comprehensive_dataset）",
    )

    test_parser = subparsers.add_parser("run-test", help="运行 SQL 压测")
    add_db_arguments(test_parser)
    test_parser.add_argument(
        "--query-type",
        choices=["qvec", "nqvec", "qft", "nqft", "mixed"],
        default="qvec",
        help="选择执行 SQL 模板",
    )
    test_parser.add_argument(
        "--table",
        default=DEFAULT_TABLE,
        help="单表模式下使用的表名（默认 ca_comprehensive_dataset）",
    )
    test_parser.add_argument(
        "--tables",
        help="多表模式下使用的表名，逗号分隔；提供时覆盖 --table",
    )
    test_parser.add_argument("--vectors-file", type=Path, help="qvec/nqvec 查询使用的向量样本文件")
    test_parser.add_argument("--tokens-file", type=Path, help="qft/nqft 查询使用的 tokens 样本文件")
    test_parser.add_argument("--concurrency", type=int, default=4, help="单类型查询的并发线程数")
    test_parser.add_argument("--duration", type=int, default=60, help="测试持续时间（秒）")
    test_parser.add_argument("--progress-interval", type=int, default=10, help="进度打印间隔（秒）")
    test_parser.add_argument("--connect-timeout", type=float, default=60.0, help="数据库连接超时时间")
    test_parser.add_argument("--read-timeout", type=float, default=30.0, help="数据库读写超时时间")
    test_parser.add_argument(
        "--type-concurrency",
        type=int,
        default=1,
        help="混合模式下每种查询的并发数",
    )
    test_parser.add_argument(
        "--mixed-types",
        default="qvec,qft",
        help="混合模式下使用的查询类型，逗号分隔，可选 qvec,qft,nqft",
    )
    test_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="打印执行的 SQL 与参数（长向量用 ... 替代）",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "download-vectors":
        download_vectors(
            count=args.count,
            batch_size=args.batch_size,
            output=args.output,
            progress_interval=max(1, args.progress_interval),
            host=args.host,
            port=args.port,
            user=args.user,
            password=args.password,
            database=args.database,
            table=args.table,
        )
        return
    if args.command == "download-tokens":
        download_tokens(
            count=args.count,
            batch_size=args.batch_size,
            offset=max(0, args.offset),
            randomize=args.randomize,
            min_line_size=max(1, args.min_line_size),
            max_line_size=max(args.min_line_size, args.max_line_size),
            tokenizer_mode=args.mode,
            min_token_length=args.min_token_length,
            output=args.output,
            progress_interval=max(1, args.progress_interval),
            host=args.host,
            port=args.port,
            user=args.user,
            password=args.password,
            database=args.database,
            table=args.table,
        )
        return
    if args.command == "run-test":
        mixed_types = [item.strip() for item in args.mixed_types.split(",")] if args.mixed_types else []
        if getattr(args, "tables", None):
            tables = [item.strip() for item in args.tables.split(",") if item.strip()]
        else:
            tables = [args.table]
        run_load_test(
            query_type=args.query_type,
            vectors_file=args.vectors_file,
            tokens_file=args.tokens_file,
            concurrency=max(1, args.concurrency),
            type_concurrency=max(1, args.type_concurrency),
            mixed_types=mixed_types,
            duration=max(1, args.duration),
            progress_interval=max(1, args.progress_interval),
            host=args.host,
            port=args.port,
            user=args.user,
            password=args.password,
            database=args.database,
            connect_timeout=args.connect_timeout,
            read_timeout=args.read_timeout,
            verbose=args.verbose,
            tables=tables,
        )
        return

    parser.error(f"未知命令: {args.command}")


if __name__ == "__main__":
    main()
