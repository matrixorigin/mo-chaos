#!/bin/bash
set -ex
exp=$1
name=$(cat name)
NAMESPACE=chaos-$name
kubectl apply -f .github/workflows/tke/$exp.yaml -n $NAMESPACE
until kubectl wait --for=condition=ready pod --selector=job-name=$exp --timeout=-1s -n $NAMESPACE
do
    sleep 5
done
kubectl logs -f job.batch/$exp -n $NAMESPACE