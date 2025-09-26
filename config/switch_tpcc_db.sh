#!/bin/bash

set -euo pipefail

# 默认连接配置（可被环境变量或命令行覆盖）
HOST=${HOST:-10.222.6.253}
PORT=${PORT:-6001}
USER=${USER:-tpcc_test:admin}
PASS=${PASS:-111}

TO_DB=""
TOGGLE=0

usage() {
  echo "Usage: $0 [--to tpcc_10|tpcc_10_bak | --toggle] [--host HOST] [--port PORT] [--user USER] [--pass PASS]" >&2
}

# 解析参数
while [[ $# -gt 0 ]]; do
  case "$1" in
    --to)
      shift
      TO_DB=${1:-}
      if [[ -z "$TO_DB" || ("$TO_DB" != "tpcc_10" && "$TO_DB" != "tpcc_10_bak") ]]; then
        echo "--to requires tpcc_10 or tpcc_10_bak" >&2
        usage
        exit 2
      fi
      ;;
    --toggle)
      TOGGLE=1
      ;;
    --host)
      shift; HOST=${1:-};;
    --port)
      shift; PORT=${1:-};;
    --user)
      shift; USER=${1:-};;
    --pass)
      shift; PASS=${1:-};;
    -h|--help)
      usage; exit 0;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
  shift || true
done

# 1) 计算下一次要切到的 DB 名称
NEXT_DB=""
if [[ -n "$TO_DB" ]]; then
  NEXT_DB="$TO_DB"
elif [[ $TOGGLE -eq 1 ]]; then
  # 基于当前软链目标进行翻转（macOS 的 readlink 无 -f，这里使用相对名判断）
  CURRENT_LINK_TARGET=$(readlink props.mo || echo "")
  if [[ "$CURRENT_LINK_TARGET" == "tpcc_10.props" ]]; then
    NEXT_DB="tpcc_10_bak"
  else
    NEXT_DB="tpcc_10"
  fi
else
  # 兼容原有逻辑：若能使用 bak，则切到 bak，否则切到 10
  if mysql -h"$HOST" -P"$PORT" -u"$USER" -p"$PASS" -e "use tpcc_10_bak; select 1;" 2>/dev/null; then
    NEXT_DB="tpcc_10_bak"
  else
    NEXT_DB="tpcc_10"
  fi
fi

# 2) 校验 props 文件是否存在（脚本需在 mo-tpcc 工作目录执行）
if [[ ! -f "${NEXT_DB}.props" ]]; then
  echo "props file not found in current dir: ${NEXT_DB}.props" >&2
  exit 3
fi

# 3) 把软链指向对应 properties 文件（保证 props.mo 始终软链到“下一次”要用的库）
ln -sf "${NEXT_DB}.props" props.mo

echo "[$(date)] switched to ${NEXT_DB} (HOST=$HOST PORT=$PORT USER=$USER)"