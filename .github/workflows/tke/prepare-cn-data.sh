#!/bin/bash
set -ex
name=$(cat name)
NAMESPACE=chaos-$name

for pod in $(kubectl get pods --selector=matrixorigin.io/component=CNSet -n $NAMESPACE --no-headers -o custom-columns=":metadata.name"); do
  kubectl exec -it $pod -n $NAMESPACE -- bash -c '
  export https_proxy=http://proxy-service.proxy.svc.cluster.local:8001
  git clone https://github.com/matrixorigin/matrixone
  '
done