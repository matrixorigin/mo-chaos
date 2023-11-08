#!/bin/sh
NAMESPACE=$1
# delete namespace
kubectl delete ns $NAMESPACE