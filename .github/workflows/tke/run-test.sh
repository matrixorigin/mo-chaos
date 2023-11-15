#!/bin/bash
name=$(cat name)
NAMESPACE=chaos-$name
export LC_ALL="C.UTF-8"
locale
sed -i "s/127.0.0.1:6001/$name-tp-cn.$NAMESPACE:6001/" ./mo-tester/mo.yml
sed -i 's/socketTimeout:.*/socketTimeout: 300000/g' ./mo-tester/mo.yml
sed -i 's/waittime:.*/waittime: 2000/g' ./mo-tester/run.yml
cat ./mo-tester/mo.yml
echo "=========================="
cat ./mo-tester/run.yml
echo "=========================="

cd $GITHUB_WORKSPACE/mo-tester
./run.sh -n -g -o -p $GITHUB_WORKSPACE/matrixone/test/distributed/cases -s $GITHUB_WORKSPACE/matrixone/test/distributed/resources -e optimistic  2>&1