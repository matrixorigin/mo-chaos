#!/bin/bash
set -e
ls
name=$(cat name)
NAMESPACE=chaos-$name
while [ 1 ]
do
  res=$(kubectl get bucketclaims.core.matrixorigin.io -n $NAMESPACE --ignore-not-found)
  if [[ $res -eq "" ]]
  then
    break
  else
    sleep 1
  fi
done
# remove mo clusterr
echo "kubectl delete matrixoneclusters.core.matrixorigin.io $name -n $NAMESPACE"
kubectl delete matrixoneclusters.core.matrixorigin.io $name -n $NAMESPACE
# delete namespace
echo "kubectl delete ns $NAMESPACE"
kubectl delete ns $NAMESPACE