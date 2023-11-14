#!/bin/bash
set -e
ls
name=$(cat name)
NAMESPACE=chaos-$name
# remove mo clusterr
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
# delete namespace
echo "kubectl delete ns $NAMESPACE"
kubectl delete ns $NAMESPACE