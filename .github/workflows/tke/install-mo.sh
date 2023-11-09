#!/bin/sh
set -e
uuid=$(cat /proc/sys/kernel/random/uuid)
cat uuid > uuid
NAMESPACE=chaos-$uuid
# create namespace
kubectl create ns $NAMESPACE
# create secret
kubectl create secret generic tencent-token -n $NAMESPACE --from-literal=AWS_ACCESS_KEY_ID='${{secrets.COS_AK}}' --from-literal=AWS_SECRET_ACCESS_KEY='${{secrets.COS_SK}}'
# create mo cluster file
appName=$uuid
imageTag=$(curl https://hub.docker.com/v2/namespaces/matrixorigin/repositories/matrixone/tags|jq -r '.results[0].name')
eval "echo \"$(cat .github/workflows/tke/mo-cluster.temp)\""
eval "echo \"$(cat .github/workflows/tke/mo-cluster.temp)\"" > mo-cluster.yaml
# create mo cluster
kubectl apply -n $NAMESPACE -f mo-cluster.yaml