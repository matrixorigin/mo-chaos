#!/bin/sh
set -e
ls
name=$(cat name)
NAMESPACE=chaos-$name
# remove mo clusterr
echo "kubectl delete -f mo-cluster.yaml -n $NAMESPACE"
kubectl delete matrixoneclusters.core.matrixorigin.io chaos -n $NAMESPACE
# delete namespace
echo "kubectl delete ns $NAMESPACE"
kubectl delete ns $NAMESPACE