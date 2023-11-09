#!/bin/sh
set -e
uuid=$(cat uuid)
NAMESPACE=chaos-$uuid
# remove mo clusterr
kubectl delete -f mo-cluster.yaml -n $NAMESPACE
# delete namespace
kubectl delete ns $NAMESPACE