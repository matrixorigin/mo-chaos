#!/bin/bash
set -e
name=$(cat name)
NAMESPACE=chaos-$name
until kubectl wait --for=condition=ready pod --selector=matrixorigin.io/component=CNSet --timeout=-1s -n $NAMESPACE
do
    sleep 5
done