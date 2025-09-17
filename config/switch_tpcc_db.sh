#!/bin/bash
HOST=10.222.6.253
PORT=6001
USER=tpcc_test:admin
PASS=111

# 1. 判断当前该用哪个库
if mysql -h$HOST -P$PORT -u$USER -p$PASS -e "use tpcc_10_bak; select 1;" 2>/dev/null
then
    NEXT_DB=tpcc_10
else
    NEXT_DB=tpcc_10_bak
fi


# 3. 把软链指向对应 properties 文件
ln -sf $NEXT_DB.props props.mo      # 保证 props.mo 始终软链到“下一次”要用的库

echo "[$(date)] switched to $NEXT_DB"