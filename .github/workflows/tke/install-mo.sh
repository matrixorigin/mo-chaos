#!/bin/bash
set -e
pfx=$(cat /dev/urandom | tr -dc 'a-z' | head -c 1)
sfx=$(cat /dev/urandom | tr -cd 'a-z0-9' | head -c 15)
name=$pfx$sfx
echo $name > name
NAMESPACE=chaos-$name
# create namespace
kubectl create ns $NAMESPACE
# create secret
kubectl create secret generic tencent-token -n $NAMESPACE --from-literal=AWS_ACCESS_KEY_ID="${COS_AK}" --from-literal=AWS_SECRET_ACCESS_KEY="${COS_SK}"

# create mo cluster file
appName=$name
curl https://hub.docker.com/v2/namespaces/matrixorigin/repositories/matrixone/tags > tags
idx=0
while [ 1 ]
do
  if [[ $(cat tags|jq -r ".results[$idx].name") != "latest" ]]; then
    imageTag=$(cat tags|jq -r ".results[$idx].name")
    break
  else
    idx=$((idx+1))
  fi
  sleep 1
done
eval "echo \"$(cat .github/workflows/tke/mo-cluster.temp)\""
eval "echo \"$(cat .github/workflows/tke/mo-cluster.temp)\"" > mo-cluster.yaml
# create mo cluster
kubectl apply -n $NAMESPACE -f mo-cluster.yaml
# create crb
kubectl create clusterrolebinding --serviceaccount $NAMESPACE:chaos-job-runner --clusterrole=tke:admin chaos-job-rb-$NAMESPACE
# create sa
kubectl create serviceaccount chaos-job-runner -n $NAMESPACE