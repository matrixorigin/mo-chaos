#!/bin/bash
name=$(cat name)
NAMESPACE=chaos-$name

for pod in $(kubectl get pods --selector=matrixorigin.io/component=CNSet -n $NAMESPACE --no-headers -o custom-columns=":metadata.name"); do
  kubectl exec -it $pod -n $NAMESPACE -- bash -c '
  set -x;
  export https_proxy=http://proxy-service.proxy.svc.cluster.local:8001
  git config --global init.defaultBranch main
  success="no"
  path_matrixone=$(pwd);
  for i in {1..5}; do
    rm -rf $path_matrixone/matrixone;
    mkdir $path_matrixone/matrixone;
    git config --global --add safe.directory $path_matrixone/matrixone;
    cd $path_matrixone/matrixone && git init;
    git remote add origin https://github.com/matrixorigin/matrixone.git;
    git config --local gc.auto 0;
    timeout 120 git -c protocol.version=2 fetch --no-tags --prune --progress --no-recurse-submodules --depth=1 origin +${{ github.sha }}:refs/remotes/origin/${{ github.ref_name }}
    git checkout --progress --force -B ${{ github.ref_name }} refs/remotes/origin/${{ github.ref_name }}
    if [ "$(git log -1 --format='%H')" == "${{ github.sha }}" ]; then
      success="yes";
      break;
    fi
    sleep 15;
  done
  if [ $success == "no" ]; then
    exit 1;
  fi
  exit 0;
  '
done