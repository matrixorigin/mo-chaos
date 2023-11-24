#!/bin/bash
set -e
exp=$1
#name=$(cat name)
name=ccpal6tim1ayi3q4
NAMESPACE=chaos-$name
kubectl apply -f .github/workflows/tke/$exp.yaml -n $NAMESPACE
until kubectl wait --for=condition=ready pod --selector=job-name=run --timeout=-1s -n $NAMESPACE
do
    sleep 5
done
kubectl logs -f job.batch/run -n $NAMESPACE