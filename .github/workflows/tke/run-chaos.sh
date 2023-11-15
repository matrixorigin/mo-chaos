#!/bin/bash
set -e
name=$(cat name)
NAMESPACE=chaos-$name
kubectl apply -f .github/workflows/tke/cn-pod-delete-job.yaml -n $NAMESPACE
until kubectl wait --for=condition=ready pod --selector=job-name=run --timeout=-1s -n $NAMESPACE
do
    sleep 5
done
kubectl logs -f job.batch/run -n $NAMESPACE