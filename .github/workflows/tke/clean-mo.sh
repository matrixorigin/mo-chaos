#!/bin/sh
set -e
sleep infinity
uuid=$(cat uuid)
NAMESPACE=chaos-$uuid
# remove mo clusterr
echo "kubectl delete -f mo-cluster.yaml -n $NAMESPACE"
#kubectl delete -f mo-cluster.yaml -n $NAMESPACE
# delete namespace
echo "kubectl delete ns $NAMESPACE"
#kubectl delete ns $NAMESPACE