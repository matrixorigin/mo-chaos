#!/bin/bash
set -ex
data_scene=$1
name=$(cat name)
NAMESPACE=chaos-$name
kubectl apply -f .github/workflows/tke/$data_scene.yaml -n $NAMESPACE
until kubectl wait --for=condition=ready pod --selector=job-name=$data_scene --timeout=-1s -n $NAMESPACE
do
    sleep 5
done
kubectl logs -f job.batch/$data_scene -n $NAMESPACE
