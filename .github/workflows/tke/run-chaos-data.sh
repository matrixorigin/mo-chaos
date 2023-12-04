#!/bin/bash
set -ex
data_scene=$1
#name=$(cat name)
name=zndmnczhyjclb13e
NAMESPACE=chaos-$name
sed -i "s%$ACTIONS_RUNNER_POD_NAME%$ACTIONS_RUNNER_POD_NAME%" .github/workflows/tke/$data_scene.yaml
sed -i "s%$GITHUB_WORKSPACE%$GITHUB_WORKSPACE%" .github/workflows/tke/$data_scene.yaml
kubectl apply -f .github/workflows/tke/$data_scene.yaml -n $NAMESPACE
until kubectl wait --for=condition=ready pod --selector=job-name=$data_scene --timeout=-1s -n $NAMESPACE
do
    sleep 5
done
kubectl logs -f job.batch/$data_scene -n $NAMESPACE
kubectl delete jobs.batch $data_scene -n $NAMESPACE