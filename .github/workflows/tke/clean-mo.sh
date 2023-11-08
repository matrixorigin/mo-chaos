#!/bin/sh
set -e
NAMESPACE=$1
# remove mo clusterr
kubectl delete -f mo-cluster.yaml -n $NAMESPACE
# delete namespace
kubectl delete ns $NAMESPACE