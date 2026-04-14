#!/usr/bin/env bash
# ============================================================
#  Extract the aks-incident-bot ServiceAccount token and
#  cluster CA so they can be added to Azure App Service
#  environment variables.
#
#  Usage:
#      chmod +x tools/get_bot_token.sh
#      ./tools/get_bot_token.sh
#
#  Pre-requisites:
#      kubectl apply -f k8s/rbac.yaml   (run first)
# ============================================================

set -euo pipefail

NAMESPACE="sre-bot"
SECRET_NAME="aks-incident-bot-token"

echo ""
echo "=== AKS Incident Bot — Kubernetes credentials ==="
echo ""

# Wait for the token to be populated by the controller
echo "Waiting for token to be issued..."
for i in $(seq 1 10); do
    TOKEN=$(kubectl get secret "$SECRET_NAME" -n "$NAMESPACE" \
        -o jsonpath='{.data.token}' 2>/dev/null | base64 --decode || true)
    if [[ -n "$TOKEN" ]]; then break; fi
    sleep 2
done

if [[ -z "$TOKEN" ]]; then
    echo "ERROR: Token not found. Run 'kubectl apply -f k8s/rbac.yaml' first."
    exit 1
fi

CA_CERT=$(kubectl get secret "$SECRET_NAME" -n "$NAMESPACE" \
    -o jsonpath='{.data.ca\.crt}' | base64 --decode)

CLUSTER_SERVER=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')

echo "Add these to Azure App Service → Configuration → Application settings:"
echo ""
echo "  K8S_API_SERVER   = $CLUSTER_SERVER"
echo "  K8S_TOKEN        = $TOKEN"
echo ""
echo "And store the CA cert as a file secret or App Service connection string:"
echo ""
echo "$CA_CERT"
echo ""
echo "Verify the bot's read-only access:"
echo "  kubectl auth can-i get pods --as=system:serviceaccount:$NAMESPACE:aks-incident-bot"
echo "  kubectl auth can-i delete pods --as=system:serviceaccount:$NAMESPACE:aks-incident-bot"
echo "  (second should return: no)"
