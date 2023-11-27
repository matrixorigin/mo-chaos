#!/bin/bash
set -ex
name=$(cat name)
NAMESPACE=chaos-$name
# remove mo cluster
echo "kubectl delete matrixoneclusters.core.matrixorigin.io $name -n $NAMESPACE"
kubectl delete matrixoneclusters.core.matrixorigin.io $name -n $NAMESPACE
# wait for it
while [ 1 ]
do
  res=$(kubectl get bucketclaims.core.matrixorigin.io -n $NAMESPACE --ignore-not-found)
  if [[ -z $res ]]
  then
    break
  else
    sleep 1
  fi
done
# delete crb
kubectl delete clusterrolebindings chaos-job-rb-chaos-ccpal6tim1ayi3q4
# delete namespace
echo "kubectl delete ns $NAMESPACE"
kubectl delete ns $NAMESPACE