"""
Kubernetes client for resource discovery.
Handles interaction with Kubernetes/OpenShift API to discover resources.
"""
from __future__ import annotations

import os
from typing import Dict, List, Any

from ..logging_utils import log_event


class KubeClient:
    """
    Client for discovering resources from Kubernetes/OpenShift.
    Uses the official kubernetes Python client if in-cluster,
    or falls back to reading kubeconfig.
    """
    
    def __init__(self):
        """Initialize the Kubernetes client."""
        self.client = None
        self.apps_api = None
        self.core_api = None
        self._initialized = False
        
        try:
            # Try to import kubernetes client
            import kubernetes
            from kubernetes import client, config
            
            # Try in-cluster config first (when running in a pod)
            try:
                config.load_incluster_config()
                log_event("kube_client.init", mode="incluster")
            except kubernetes.config.ConfigException:
                # Fall back to kubeconfig file
                try:
                    config.load_kube_config()
                    log_event("kube_client.init", mode="kubeconfig")
                except Exception as e:
                    log_event("kube_client.init.error", error=str(e))
                    # Client will remain None, methods will handle gracefully
                    return
            
            # Initialize API clients
            self.apps_api = client.AppsV1Api()
            self.core_api = client.CoreV1Api()
            self._initialized = True
            log_event("kube_client.init.success")
            
        except ImportError:
            log_event("kube_client.init.no_module", error="kubernetes module not installed")
        except Exception as e:
            log_event("kube_client.init.error", error=str(e))
    
    def is_available(self) -> bool:
        """Check if Kubernetes client is available and initialized."""
        return self._initialized
    
    def list_namespaces(self) -> List[str]:
        """
        List all namespaces in the cluster.
        
        Returns:
            List of namespace names
        """
        if not self.is_available():
            log_event("kube_client.list_namespaces.unavailable")
            return []
        
        try:
            namespaces = self.core_api.list_namespace()
            result = [ns.metadata.name for ns in namespaces.items]
            log_event("kube_client.list_namespaces.success", count=len(result))
            return result
        except Exception as e:
            log_event("kube_client.list_namespaces.error", error=str(e))
            return []
    
    def list_resources(self, namespace: str, kind: str) -> Dict[str, List[str]]:
        """
        List resources of a specific kind in a namespace.
        
        Args:
            namespace: Namespace to search in
            kind: Resource kind (deployment, statefulset, daemonset)
        
        Returns:
            Dict mapping resource_name -> list of container images
            Example: {"my-app": ["nginx:1.21", "redis:7.0"]}
        """
        if not self.is_available():
            return {}
        
        try:
            resources = {}
            
            if kind.lower() == "deployment":
                items = self.apps_api.list_namespaced_deployment(namespace).items
            elif kind.lower() == "statefulset":
                items = self.apps_api.list_namespaced_stateful_set(namespace).items
            elif kind.lower() == "daemonset":
                items = self.apps_api.list_namespaced_daemon_set(namespace).items
            else:
                log_event("kube_client.list_resources.unknown_kind", kind=kind)
                return {}
            
            for item in items:
                resource_name = item.metadata.name
                images = []
                
                # Extract container images from pod spec
                if hasattr(item.spec, 'template') and hasattr(item.spec.template, 'spec'):
                    containers = item.spec.template.spec.containers or []
                    for container in containers:
                        if container.image:
                            images.append(container.image)
                
                if images:
                    resources[resource_name] = images
            
            log_event(
                "kube_client.list_resources.success",
                namespace=namespace,
                kind=kind,
                count=len(resources)
            )
            return resources
            
        except Exception as e:
            log_event(
                "kube_client.list_resources.error",
                namespace=namespace,
                kind=kind,
                error=str(e)
            )
            return {}
    
    def get_resource_details(self, namespace: str, kind: str, name: str) -> Dict[str, Any]:
        """
        Get detailed information about a specific resource.
        
        Args:
            namespace: Namespace
            kind: Resource kind
            name: Resource name
        
        Returns:
            Dict with resource details including containers
        """
        if not self.is_available():
            return {}
        
        try:
            if kind.lower() == "deployment":
                resource = self.apps_api.read_namespaced_deployment(name, namespace)
            elif kind.lower() == "statefulset":
                resource = self.apps_api.read_namespaced_stateful_set(name, namespace)
            elif kind.lower() == "daemonset":
                resource = self.apps_api.read_namespaced_daemon_set(name, namespace)
            else:
                return {}
            
            containers = []
            if hasattr(resource.spec, 'template') and hasattr(resource.spec.template, 'spec'):
                for container in resource.spec.template.spec.containers or []:
                    containers.append({
                        "name": container.name,
                        "image": container.image
                    })
            
            return {
                "name": resource.metadata.name,
                "namespace": resource.metadata.namespace,
                "kind": kind,
                "containers": containers
            }
            
        except Exception as e:
            log_event(
                "kube_client.get_resource_details.error",
                namespace=namespace,
                kind=kind,
                name=name,
                error=str(e)
            )
            return {}

