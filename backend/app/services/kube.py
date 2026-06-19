from __future__ import annotations

from typing import Optional, List, Dict, Any

from kubernetes import client, config
from kubernetes.client import ApiClient, ApiException

from ..config import settings
from ..logging_utils import log_event

# Extra vendor-specific annotation key prefixes to strip from cached manifests,
# configured via MANIFEST_STRIP_ANNOTATION_PREFIXES (empty by default → strip nothing extra).
_EXTRA_ANN = tuple(settings.manifest_strip_annotation_prefixes)


class KubeClient:
    def __init__(self) -> None:
        if settings.kube_incluster:
            config.load_incluster_config()
        else:
            if settings.kubecfg_path:
                config.load_kube_config(config_file=settings.kubecfg_path)
            else:
                config.load_kube_config()
        self.api_client: ApiClient = ApiClient()
        self.apps = client.AppsV1Api(self.api_client)
        self.core = client.CoreV1Api(self.api_client)
        self.batch = client.BatchV1Api(self.api_client)
        self.policy = client.PolicyV1Api(self.api_client)
        self.autoscaling = client.AutoscalingV2Api(self.api_client)
        self.custom = client.CustomObjectsApi(self.api_client)

    def get_image_for_resource(self, namespace: str, name: str, kind: str) -> Optional[str]:
        kind_lower = kind.strip().lower()
        try:
            if kind_lower == "deployment":
                dep = self.apps.read_namespaced_deployment(name=name, namespace=namespace)
                return _image_from_pod_spec(dep.spec.template.spec)
            if kind_lower == "statefulset":
                st = self.apps.read_namespaced_stateful_set(name=name, namespace=namespace)
                return _image_from_pod_spec(st.spec.template.spec)
            if kind_lower == "daemonset":
                ds = self.apps.read_namespaced_daemon_set(name=name, namespace=namespace)
                return _image_from_pod_spec(ds.spec.template.spec)
            if kind_lower == "job":
                job = self.batch.read_namespaced_job(name=name, namespace=namespace)
                return _image_from_pod_spec(job.spec.template.spec)
            if kind_lower == "cronjob":
                cj = self.batch.read_namespaced_cron_job(name=name, namespace=namespace)
                return _image_from_pod_spec(cj.spec.job_template.spec.template.spec)
            if kind_lower == "pod":
                pod = self.core.read_namespaced_pod(name=name, namespace=namespace)
                return _image_from_pod_spec(pod.spec)
        except ApiException as ex:  # type: ignore
            if getattr(ex, "status", None) == 404:
                log_event("kube.image.missing", kind=kind, namespace=namespace, name=name)
                return None
            raise
        # Extend as needed (e.g., OpenShift DeploymentConfig via custom client)
        return None

    def get_images_for_resource(self, namespace: str, name: str, kind: str) -> List[Dict[str, str]]:
        """Return all regular containers' names and images for a resource.

        Shape: [{"name": <container name>, "image": <image>}]
        """
        kind_lower = kind.strip().lower()
        try:
            if kind_lower == "deployment":
                dep = self.apps.read_namespaced_deployment(name=name, namespace=namespace)
                return _images_from_pod_spec(dep.spec.template.spec)
            if kind_lower == "statefulset":
                st = self.apps.read_namespaced_stateful_set(name=name, namespace=namespace)
                return _images_from_pod_spec(st.spec.template.spec)
            if kind_lower == "daemonset":
                ds = self.apps.read_namespaced_daemon_set(name=name, namespace=namespace)
                return _images_from_pod_spec(ds.spec.template.spec)
            if kind_lower == "job":
                job = self.batch.read_namespaced_job(name=name, namespace=namespace)
                return _images_from_pod_spec(job.spec.template.spec)
            if kind_lower == "cronjob":
                cj = self.batch.read_namespaced_cron_job(name=name, namespace=namespace)
                return _images_from_pod_spec(cj.spec.job_template.spec.template.spec)
            if kind_lower == "pod":
                pod = self.core.read_namespaced_pod(name=name, namespace=namespace)
                return _images_from_pod_spec(pod.spec)
        except ApiException as ex:  # type: ignore
            if getattr(ex, "status", None) == 404:
                log_event("kube.image.missing", kind=kind, namespace=namespace, name=name)
                return []
            raise
        return []

    def resource_exists(self, namespace: str, name: str, kind: str) -> bool:
        try:
            kind_lower = kind.strip().lower()
            if kind_lower == "deployment":
                self.apps.read_namespaced_deployment(name=name, namespace=namespace)
                log_event("kube.exists", kind=kind, namespace=namespace, name=name, exists=True)
                return True
            if kind_lower == "statefulset":
                self.apps.read_namespaced_stateful_set(name=name, namespace=namespace)
                log_event("kube.exists", kind=kind, namespace=namespace, name=name, exists=True)
                return True
            if kind_lower == "daemonset":
                self.apps.read_namespaced_daemon_set(name=name, namespace=namespace)
                log_event("kube.exists", kind=kind, namespace=namespace, name=name, exists=True)
                return True
            if kind_lower == "job":
                self.batch.read_namespaced_job(name=name, namespace=namespace)
                log_event("kube.exists", kind=kind, namespace=namespace, name=name, exists=True)
                return True
            if kind_lower == "cronjob":
                self.batch.read_namespaced_cron_job(name=name, namespace=namespace)
                log_event("kube.exists", kind=kind, namespace=namespace, name=name, exists=True)
                return True
            if kind_lower == "pod":
                self.core.read_namespaced_pod(name=name, namespace=namespace)
                log_event("kube.exists", kind=kind, namespace=namespace, name=name, exists=True)
                return True
        except ApiException as ex:  # type: ignore
            if getattr(ex, "status", None) == 404:
                log_event("kube.exists", kind=kind, namespace=namespace, name=name, exists=False)
                return False
            return False
        except Exception:
            return False
        return False

    def get_replicas_for_resource(self, namespace: str, name: str, kind: str) -> Optional[int]:
        """Get the current replica count for a resource.
        
        Returns None if resource doesn't support replicas or doesn't exist.
        Returns 0 if replicas is explicitly set to 0 (scaled down).
        """
        kind_lower = kind.strip().lower()
        try:
            if kind_lower == "deployment":
                dep = self.apps.read_namespaced_deployment(name=name, namespace=namespace)
                replicas = getattr(dep.status, "replicas", None)
                # Also check spec.replicas for desired count if status not available
                if replicas is None:
                    replicas = getattr(dep.spec, "replicas", None)
                return replicas if replicas is not None else 0
            if kind_lower == "statefulset":
                st = self.apps.read_namespaced_stateful_set(name=name, namespace=namespace)
                replicas = getattr(st.status, "replicas", None)
                if replicas is None:
                    replicas = getattr(st.spec, "replicas", None)
                return replicas if replicas is not None else 0
            if kind_lower == "daemonset":
                # DaemonSets don't have replica count - use number_ready
                ds = self.apps.read_namespaced_daemon_set(name=name, namespace=namespace)
                return getattr(ds.status, "number_ready", 0)
            # Jobs, CronJobs, and Pods don't have meaningful replica counts
            return None
        except ApiException as ex:  # type: ignore
            if getattr(ex, "status", None) == 404:
                log_event("kube.replicas.missing", kind=kind, namespace=namespace, name=name)
                return None
            log_event("kube.replicas.error", kind=kind, namespace=namespace, name=name, error=str(ex))
            return None
        except Exception as e:
            log_event("kube.replicas.error", kind=kind, namespace=namespace, name=name, error=str(e))
            return None


    def get_resource_manifest(self, namespace: str, name: str, kind: str) -> Optional[Dict]:
        """Get full resource manifest as dictionary (cleaned for comparison).
        
        Returns the resource as a dict with runtime fields removed:
        - metadata.managedFields
        - metadata.generation
        - metadata.creationTimestamp
        - metadata.resourceVersion
        - metadata.uid
        - metadata.selfLink
        - status (entire section)
        """
        from kubernetes.client import ApiClient
        
        kind_lower = kind.strip().lower()
        resource = None
        
        try:
            if kind_lower == "deployment":
                resource = self.apps.read_namespaced_deployment(name=name, namespace=namespace)
            elif kind_lower == "statefulset":
                resource = self.apps.read_namespaced_stateful_set(name=name, namespace=namespace)
            elif kind_lower == "daemonset":
                resource = self.apps.read_namespaced_daemon_set(name=name, namespace=namespace)
            elif kind_lower == "job":
                resource = self.batch.read_namespaced_job(name=name, namespace=namespace)
            elif kind_lower == "cronjob":
                resource = self.batch.read_namespaced_cron_job(name=name, namespace=namespace)
            elif kind_lower == "pod":
                resource = self.core.read_namespaced_pod(name=name, namespace=namespace)
            else:
                return None
        except ApiException as ex:
            if getattr(ex, "status", None) == 404:
                log_event("kube.manifest.missing", kind=kind, namespace=namespace, name=name)
                return None
            raise
        
        if not resource:
            return None
        
        # Convert to dict
        api_client = ApiClient()
        resource_dict = api_client.sanitize_for_serialization(resource)
        
        # Clean runtime fields
        return _clean_manifest_for_storage(resource_dict)

    def get_related_objects(self, namespace: str, workload_name: str, workload_manifest: Dict = None) -> Dict[str, Any]:
        """Get related Kubernetes objects for a workload.
        
        For comparison purposes - ALL objects now use full manifest comparison:
        - PDB, HPA: Full manifest comparison
        - Service: Full manifest comparison (clusterIP excluded)
        - ConfigMap, Secret: Full manifest comparison (data hashed for security)
        - Route: Full manifest comparison (migration labels excluded)
        - PVC: Full manifest comparison (volumeName normalized)
        
        Args:
            namespace: Kubernetes namespace
            workload_name: Name of the workload (deployment/statefulset/daemonset)
            workload_manifest: Optional workload manifest to extract referenced secrets/configmaps
        
        Returns dict with:
        - pdb: list of full PDB manifests
        - hpa: list of full HPA manifests  
        - service: list of full Service manifests (cleaned)
        - configmap: list of full ConfigMap manifests (data hashed)
        - secret: list of full Secret manifests (data hashed)
        - route: list of full Route manifests (cleaned)
        - pvc: list of full PVC manifests (cleaned)
        """
        related: Dict[str, Any] = {
            "pdb": [],
            "hpa": [],
            "service": [],      # Full manifests
            "configmap": [],    # Full manifests (data hashed)
            "secret": [],       # Full manifests (data hashed)
            "route": [],        # Full manifests
            "pvc": []           # Full manifests
        }
        
        api_client = ApiClient()
        
        # Helper to check if name matches workload
        def name_matches(obj_name: str) -> bool:
            return obj_name == workload_name or obj_name.startswith(f"{workload_name}-")
        
        # Extract referenced secrets, configmaps, and PVCs from workload manifest
        referenced_secrets = set()
        referenced_configmaps = set()
        referenced_pvcs = set()
        
        if workload_manifest:
            try:
                # Check pod template spec
                pod_spec = workload_manifest.get("spec", {}).get("template", {}).get("spec", {})
                
                # From volumes
                for vol in pod_spec.get("volumes", []):
                    if vol.get("secret"):
                        referenced_secrets.add(vol["secret"].get("secretName", ""))
                    if vol.get("configMap"):
                        referenced_configmaps.add(vol["configMap"].get("name", ""))
                    # PVC references
                    if vol.get("persistentVolumeClaim"):
                        referenced_pvcs.add(vol["persistentVolumeClaim"].get("claimName", ""))
                
                # From containers env and envFrom
                for container in pod_spec.get("containers", []) + pod_spec.get("initContainers", []):
                    # envFrom
                    for env_from in container.get("envFrom", []):
                        if env_from.get("secretRef"):
                            referenced_secrets.add(env_from["secretRef"].get("name", ""))
                        if env_from.get("configMapRef"):
                            referenced_configmaps.add(env_from["configMapRef"].get("name", ""))
                    # env valueFrom
                    for env in container.get("env", []):
                        value_from = env.get("valueFrom", {})
                        if value_from.get("secretKeyRef"):
                            referenced_secrets.add(value_from["secretKeyRef"].get("name", ""))
                        if value_from.get("configMapKeyRef"):
                            referenced_configmaps.add(value_from["configMapKeyRef"].get("name", ""))
                
                # From imagePullSecrets
                for ips in pod_spec.get("imagePullSecrets", []):
                    if ips.get("name"):
                        referenced_secrets.add(ips["name"])
                
                # For StatefulSet: volumeClaimTemplates
                vct = workload_manifest.get("spec", {}).get("volumeClaimTemplates", [])
                for template in vct:
                    pvc_name = template.get("metadata", {}).get("name", "")
                    if pvc_name:
                        # StatefulSet PVC naming: {pvc_name}-{workload_name}-{ordinal}
                        # We'll check for the base pattern
                        referenced_pvcs.add(f"{pvc_name}-{workload_name}")
                        
            except Exception as e:
                log_event("kube.related.parse_refs.error", namespace=namespace, workload=workload_name, error=str(e))
        
        # Remove empty strings
        referenced_secrets.discard("")
        referenced_configmaps.discard("")
        referenced_pvcs.discard("")
        
        # PodDisruptionBudget - Full manifest comparison
        try:
            pdbs = self.policy.list_namespaced_pod_disruption_budget(namespace=namespace)
            for pdb in pdbs.items:
                if name_matches(pdb.metadata.name):
                    pdb_dict = api_client.sanitize_for_serialization(pdb)
                    related["pdb"].append(_clean_manifest_for_storage(pdb_dict))
        except Exception as e:
            log_event("kube.related.pdb.error", namespace=namespace, workload=workload_name, error=str(e))
        
        # HorizontalPodAutoscaler - Full manifest comparison
        try:
            hpas = self.autoscaling.list_namespaced_horizontal_pod_autoscaler(namespace=namespace)
            for hpa in hpas.items:
                target_name = None
                if hpa.spec and hpa.spec.scale_target_ref:
                    target_name = hpa.spec.scale_target_ref.name
                if target_name == workload_name or name_matches(hpa.metadata.name):
                    hpa_dict = api_client.sanitize_for_serialization(hpa)
                    related["hpa"].append(_clean_manifest_for_storage(hpa_dict))
        except Exception as e:
            log_event("kube.related.hpa.error", namespace=namespace, workload=workload_name, error=str(e))
        
        # Service - Full manifest comparison
        try:
            services = self.core.list_namespaced_service(namespace=namespace)
            for svc in services.items:
                if name_matches(svc.metadata.name):
                    svc_dict = api_client.sanitize_for_serialization(svc)
                    related["service"].append(_clean_service_for_comparison(svc_dict))
        except Exception as e:
            log_event("kube.related.service.error", namespace=namespace, workload=workload_name, error=str(e))
        
        # ConfigMap - Full manifest comparison (data hashed)
        collected_cm_names = set()
        try:
            cms = self.core.list_namespaced_config_map(namespace=namespace)
            cms_by_name = {cm.metadata.name: cm for cm in cms.items}
            
            # Collect referenced configmaps
            for cm_name in referenced_configmaps:
                if cm_name in cms_by_name and cm_name not in collected_cm_names:
                    cm = cms_by_name[cm_name]
                    cm_dict = api_client.sanitize_for_serialization(cm)
                    related["configmap"].append(_clean_configmap_for_comparison(cm_dict))
                    collected_cm_names.add(cm_name)
            
            # Also add name-matched ones
            for cm in cms.items:
                if name_matches(cm.metadata.name) and cm.metadata.name not in collected_cm_names:
                    cm_dict = api_client.sanitize_for_serialization(cm)
                    related["configmap"].append(_clean_configmap_for_comparison(cm_dict))
                    collected_cm_names.add(cm.metadata.name)
        except Exception as e:
            log_event("kube.related.configmap.error", namespace=namespace, workload=workload_name, error=str(e))
        
        # Secret - Full manifest comparison (data hashed)
        collected_secret_names = set()
        try:
            secrets = self.core.list_namespaced_secret(namespace=namespace)
            secrets_by_name = {s.metadata.name: s for s in secrets.items}
            
            # Collect referenced secrets
            for secret_name in referenced_secrets:
                if secret_name in secrets_by_name and secret_name not in collected_secret_names:
                    secret = secrets_by_name[secret_name]
                    secret_dict = api_client.sanitize_for_serialization(secret)
                    related["secret"].append(_clean_secret_for_comparison(secret_dict))
                    collected_secret_names.add(secret_name)
            
            # Also add name-matched ones
            for s in secrets.items:
                if name_matches(s.metadata.name) and s.metadata.name not in collected_secret_names:
                    secret_dict = api_client.sanitize_for_serialization(s)
                    related["secret"].append(_clean_secret_for_comparison(secret_dict))
                    collected_secret_names.add(s.metadata.name)
        except Exception as e:
            log_event("kube.related.secret.error", namespace=namespace, workload=workload_name, error=str(e))
        
        # Route - Full manifest comparison (OpenShift specific)
        try:
            routes = self.custom.list_namespaced_custom_object(
                group="route.openshift.io",
                version="v1",
                namespace=namespace,
                plural="routes"
            )
            for route in routes.get("items", []):
                route_name = route.get("metadata", {}).get("name", "")
                if name_matches(route_name):
                    related["route"].append(_clean_route_for_comparison(route))
        except ApiException as e:
            if e.status != 404:
                log_event("kube.related.route.error", namespace=namespace, workload=workload_name, error=str(e))
        except Exception as e:
            log_event("kube.related.route.error", namespace=namespace, workload=workload_name, error=str(e))
        
        # PVC - Full manifest comparison
        collected_pvc_names = set()
        try:
            pvcs = self.core.list_namespaced_persistent_volume_claim(namespace=namespace)
            pvcs_by_name = {pvc.metadata.name: pvc for pvc in pvcs.items}
            
            # Check exact matches first
            for pvc_name in referenced_pvcs:
                if pvc_name in pvcs_by_name and pvc_name not in collected_pvc_names:
                    pvc = pvcs_by_name[pvc_name]
                    pvc_dict = api_client.sanitize_for_serialization(pvc)
                    related["pvc"].append(_clean_pvc_for_comparison(pvc_dict))
                    collected_pvc_names.add(pvc_name)
                else:
                    # For StatefulSet, check pattern: {base}-{ordinal}
                    for existing_pvc_name, pvc in pvcs_by_name.items():
                        if (existing_pvc_name.startswith(pvc_name + "-") or existing_pvc_name == pvc_name):
                            if existing_pvc_name not in collected_pvc_names:
                                pvc_dict = api_client.sanitize_for_serialization(pvc)
                                related["pvc"].append(_clean_pvc_for_comparison(pvc_dict))
                                collected_pvc_names.add(existing_pvc_name)
            
            # Also add name-matched PVCs
            for pvc in pvcs.items:
                if name_matches(pvc.metadata.name) and pvc.metadata.name not in collected_pvc_names:
                    pvc_dict = api_client.sanitize_for_serialization(pvc)
                    related["pvc"].append(_clean_pvc_for_comparison(pvc_dict))
                    collected_pvc_names.add(pvc.metadata.name)
        except Exception as e:
            log_event("kube.related.pvc.error", namespace=namespace, workload=workload_name, error=str(e))
        
        # Sort manifest lists by name for consistent comparison
        for rel_type in ["service", "configmap", "secret", "route", "pvc"]:
            related[rel_type].sort(key=lambda x: x.get("metadata", {}).get("name", ""))

        return related

    # ------------------------------------------------------------------
    # Helm release status (Phase 1A): drift of the live manifest vs what
    # Helm last rendered for this resource. No `helm` binary — we read and
    # decode the release Secret directly.
    # ------------------------------------------------------------------
    def helm_latest_revision(self, release_namespace: str, release_name: str) -> Optional[int]:
        """Cheapest possible: highest `version` label across the release's owner=helm
        Secrets (no data.release gunzip). Used by the refresh dedupe."""
        secret, revision = self._latest_helm_release_secret(release_namespace, release_name)
        return revision

    def _latest_helm_release_secret(self, release_namespace: str, release_name: str):
        """Return (secret, revision) for the highest-`version` owner=helm release Secret,
        or (None, None) if none is readable."""
        try:
            secrets = self.core.list_namespaced_secret(
                release_namespace,
                label_selector=f"owner=helm,name={release_name}",
            )
            best, best_rev = None, -1
            for s in (secrets.items or []):
                labels = (s.metadata.labels or {}) if s.metadata else {}
                try:
                    rev = int(labels.get("version"))
                except (TypeError, ValueError):
                    continue
                if rev > best_rev:
                    best, best_rev = s, rev
            return (best, best_rev) if best is not None else (None, None)
        except Exception as e:
            log_event("helm.release_secret.error", namespace=release_namespace,
                      release=release_name, error=str(e))
            return None, None

    def get_helm_status(self, namespace: str, name: str, kind: str,
                        live_manifest: Optional[Dict], has_hpa: bool = False,
                        release_cache: Optional[Dict] = None) -> Dict[str, Any]:
        """Compute the Helm drift status of a resource.

        Returns {status, release_name, release_namespace, revision, drift_detail} where
        status is 'ok' | 'drift' | 'none' (not Helm-managed) | 'unknown' (couldn't
        determine). Never raises. `release_cache` (a dict) decodes each release once per
        refresh pass. The caller (refresh) decides WHEN to call this (force/changed gate)."""
        result: Dict[str, Any] = {"status": "none", "release_name": None,
                                  "release_namespace": None, "revision": None,
                                  "drift_detail": None}
        try:
            md = (live_manifest or {}).get("metadata") or {}
            ann = md.get("annotations") or {}
            release_name = ann.get("meta.helm.sh/release-name")
            release_ns = ann.get("meta.helm.sh/release-namespace")
            if not release_name or not release_ns:
                return result  # not Helm-managed
            result["release_name"] = release_name
            result["release_namespace"] = release_ns
            result["status"] = "unknown"

            cache_key = f"{release_ns}/{release_name}"
            if release_cache is not None and cache_key in release_cache:
                rendered, revision = release_cache[cache_key]
            else:
                secret, revision = self._latest_helm_release_secret(release_ns, release_name)
                rendered = _decode_helm_release_manifests(secret) if secret is not None else None
                if release_cache is not None:
                    release_cache[cache_key] = (rendered, revision)
            result["revision"] = revision
            if not rendered:
                # Helm-managed (by annotation) but we couldn't get a release manifest.
                # Distinguish the cases so "?" resources are diagnosable from the log.
                reason = "no_release_secret" if revision is None else "release_decode_failed"
                log_event("helm.status.unknown", namespace=namespace, resource=name,
                          release=release_name, release_namespace=release_ns, reason=reason)
                return result

            helm_doc = _find_rendered_doc(rendered, kind, name, namespace)
            if helm_doc is None:
                log_event("helm.status.unknown", namespace=namespace, resource=name,
                          release=release_name, release_namespace=release_ns,
                          reason="doc_not_found_in_release", revision=revision)
                return result  # unknown: resource not present in the release manifest

            # Normalize both the same way (the live manifest is already cleaned), then
            # project only the patchable fields and diff.
            helm_proj = _build_patchable_projection(_clean_manifest_for_storage(helm_doc),
                                                    include_replicas=not has_hpa)
            live_proj = _build_patchable_projection(live_manifest or {},
                                                    include_replicas=not has_hpa)
            drift = _diff_projections(helm_proj, live_proj)
            result["status"] = "drift" if drift else "ok"
            result["drift_detail"] = drift
            return result
        except Exception as e:
            log_event("helm.status.error", namespace=namespace, resource=name, error=str(e))
            result["status"] = "unknown"
            return result


def _clean_manifest_for_storage(obj: Dict) -> Dict:
    """Remove runtime fields from manifest for storage and comparison."""
    import copy
    
    result = copy.deepcopy(obj)
    
    # Remove status entirely
    result.pop('status', None)
    
    # Clean metadata
    if 'metadata' in result and isinstance(result['metadata'], dict):
        runtime_fields = ['managedFields', 'generation', 'creationTimestamp', 
                          'resourceVersion', 'uid', 'selfLink', 'ownerReferences', 'namespace']
        for field in runtime_fields:
            result['metadata'].pop(field, None)
        
        # Remove auto-generated/migration/deployment annotations
        if 'annotations' in result['metadata'] and isinstance(result['metadata']['annotations'], dict):
            ann_to_remove = [k for k in result['metadata']['annotations'].keys()
                           if k == 'kubectl.kubernetes.io/last-applied-configuration'
                           or k.startswith('openshift.io/migration')
                           or k.startswith('openshift.io/backup-')   # Backup annotations
                           or k.startswith('openshift.io/restore-')  # Restore annotations
                           or k.startswith('deployment.kubernetes.io/')  # Auto-generated revision etc.
                           or (_EXTRA_ANN and k.startswith(_EXTRA_ANN))]  # Vendor-specific annotations
            for key in ann_to_remove:
                del result['metadata']['annotations'][key]
            if not result['metadata']['annotations']:
                del result['metadata']['annotations']
        
        # Remove migration/velero labels
        if 'labels' in result['metadata'] and isinstance(result['metadata']['labels'], dict):
            labels_to_remove = [k for k in result['metadata']['labels'].keys() 
                               if k.startswith('migration.openshift.io/') or k.startswith('velero.io/')]
            for key in labels_to_remove:
                del result['metadata']['labels'][key]
            if not result['metadata']['labels']:
                del result['metadata']['labels']
    
    # Clean spec.template.metadata (for Deployment/StatefulSet/DaemonSet pod templates)
    if 'spec' in result and isinstance(result['spec'], dict):
        template = result['spec'].get('template', {})
        if isinstance(template, dict) and 'metadata' in template:
            tmpl_meta = template['metadata']
            
            # Remove runtime fields from pod template metadata
            for field in ['managedFields', 'creationTimestamp', 'resourceVersion', 'uid']:
                tmpl_meta.pop(field, None) if isinstance(tmpl_meta, dict) else None
            
            # Remove deployment-specific annotations from pod template
            if isinstance(tmpl_meta, dict) and 'annotations' in tmpl_meta and isinstance(tmpl_meta['annotations'], dict):
                ann_to_remove = [k for k in tmpl_meta['annotations'].keys()
                               if k == 'kubectl.kubernetes.io/last-applied-configuration'
                               or k == 'kubectl.kubernetes.io/restartedAt'  # rollout restart timestamp
                               or k.startswith('openshift.io/migration')
                               or k.startswith('openshift.io/backup-')
                               or k.startswith('openshift.io/restore-')
                               or (_EXTRA_ANN and k.startswith(_EXTRA_ANN))]
                for key in ann_to_remove:
                    del tmpl_meta['annotations'][key]
                if not tmpl_meta['annotations']:
                    del tmpl_meta['annotations']
            
            # Remove migration/velero labels from pod template
            if isinstance(tmpl_meta, dict) and 'labels' in tmpl_meta and isinstance(tmpl_meta['labels'], dict):
                labels_to_remove = [k for k in tmpl_meta['labels'].keys() 
                                   if k.startswith('migration.openshift.io/') or k.startswith('velero.io/')]
                for key in labels_to_remove:
                    del tmpl_meta['labels'][key]
                if not tmpl_meta['labels']:
                    del tmpl_meta['labels']
    
    return result


# =============================================================================
# Helm release status helpers (Phase 1A)
# =============================================================================

def _decode_helm_release_manifests(secret) -> Optional[List[Dict]]:
    """Decode a Helm release Secret into the list of rendered manifest docs.
    Helm stores the release double-base64'd + gzipped JSON; the rendered manifest is
    the `manifest` field (a multi-doc YAML string). Returns None on any failure."""
    import base64
    import gzip
    import json as _json
    try:
        import yaml
        data = (getattr(secret, "data", None) or {})
        raw = data.get("release")
        if not raw:
            return None
        # Secret data is base64 (API level); Helm's stored value is itself base64(gzip(json)).
        blob = base64.b64decode(raw)
        try:
            blob = base64.b64decode(blob)  # Helm's second base64 layer
        except Exception:
            pass  # already gzip bytes (be tolerant of single-encoded variants)
        release = _json.loads(gzip.decompress(blob))
        manifest_str = release.get("manifest") or release.get("manifests") or ""
        return [d for d in yaml.safe_load_all(manifest_str) if isinstance(d, dict)]
    except Exception as e:
        log_event("helm.release.decode_error", error=str(e))
        return None


def _find_rendered_doc(docs: List[Dict], kind: str, name: str, namespace: str) -> Optional[Dict]:
    """Find the rendered doc matching (kind, name) for this resource. Helm rendered docs
    may omit namespace; if present it must match."""
    kl = (kind or "").strip().lower()
    for d in docs:
        try:
            if (d.get("kind") or "").strip().lower() != kl:
                continue
            md = d.get("metadata") or {}
            if md.get("name") != name:
                continue
            ns = md.get("namespace")
            if ns and ns != namespace:
                continue
            return d
        except Exception:
            continue
    return None


_MEM_UNITS = (("Ki", 1024), ("Mi", 1024 ** 2), ("Gi", 1024 ** 3), ("Ti", 1024 ** 4),
              ("Pi", 1024 ** 5), ("Ei", 1024 ** 6), ("k", 1000), ("M", 1000 ** 2),
              ("G", 1000 ** 3), ("T", 1000 ** 4), ("P", 1000 ** 5), ("E", 1000 ** 6))


def _norm_quantity(unit: str, val) -> Optional[str]:
    """Canonicalize a K8s resource quantity so equal values don't read as drift
    (cpu -> millicores, memory -> bytes). Falls back to the raw string on parse error."""
    if val is None:
        return None
    s = str(val).strip()
    try:
        if unit == "cpu":
            if s.endswith("m"):
                return f"{int(float(s[:-1]))}m"
            return f"{int(float(s) * 1000)}m"
        for suf, mult in _MEM_UNITS:
            if s.endswith(suf):
                return str(int(float(s[:-len(suf)]) * mult))
        return str(int(float(s)))
    except Exception:
        return s


# Labels/annotations injected by Helm/Kubernetes/controllers — excluded from the drift
# comparison so they don't read as false-positive drift.
_DRIFT_ANN_IGNORE_PREFIXES = ("meta.helm.sh/", "kubectl.kubernetes.io/",
                              "deployment.kubernetes.io/", "kubernetes.io/",
                              "k8s.io/", "openshift.io/")
_DRIFT_LABEL_IGNORE_PREFIXES = ("statefulset.kubernetes.io/",)
_DRIFT_LABEL_IGNORE_EXACT = {"pod-template-hash", "controller-revision-hash"}


def _meta_into_projection(meta: Dict, prefix: str, proj: Dict) -> None:
    if not isinstance(meta, dict):
        return
    for k, v in (meta.get("labels") or {}).items():
        if k in _DRIFT_LABEL_IGNORE_EXACT or any(k.startswith(p) for p in _DRIFT_LABEL_IGNORE_PREFIXES):
            continue
        proj[f"{prefix}labels[{k}]"] = v
    for k, v in (meta.get("annotations") or {}).items():
        if any(k == p or k.startswith(p) for p in _DRIFT_ANN_IGNORE_PREFIXES):
            continue
        proj[f"{prefix}annotations[{k}]"] = v


def _build_patchable_projection(manifest: Dict, include_replicas: bool = True) -> Dict:
    """Flatten the patchable fields of a workload manifest to a {path: value} dict so two
    manifests can be diffed field-by-field. Covers exactly what Phase 1 can patch:
    per-container image/env/resources/command/args, replicas, and (filtered)
    labels/annotations on the workload + pod template."""
    proj: Dict = {}
    spec = manifest.get("spec") or {}
    if include_replicas and "replicas" in spec:
        proj["replicas"] = spec.get("replicas")

    _meta_into_projection(manifest.get("metadata") or {}, "", proj)

    template = spec.get("template") or {}
    _meta_into_projection(template.get("metadata") or {}, "pod.", proj)
    pod_spec = template.get("spec") or {}
    for c in (pod_spec.get("containers") or []):
        if not isinstance(c, dict):
            continue
        base = f"container[{c.get('name')}]"
        proj[f"{base}.image"] = c.get("image")
        if c.get("command") is not None:
            proj[f"{base}.command"] = c.get("command")
        if c.get("args") is not None:
            proj[f"{base}.args"] = c.get("args")
        for e in (c.get("env") or []):
            if not isinstance(e, dict):
                continue
            en = e.get("name")
            if "value" in e:
                proj[f"{base}.env[{en}]"] = e.get("value")
            elif e.get("valueFrom"):
                proj[f"{base}.env[{en}]"] = "<valueFrom>"  # reference, not resolved (no secrets)
        env_from = c.get("envFrom")
        if env_from:
            refs = []
            for ef in env_from:
                src = (ef.get("configMapRef") or ef.get("secretRef") or {}) if isinstance(ef, dict) else {}
                if src.get("name"):
                    refs.append(src["name"])
            proj[f"{base}.envFrom"] = sorted(refs)
        res = c.get("resources") or {}
        for rk in ("requests", "limits"):
            block = res.get(rk) or {}
            for q in ("cpu", "memory"):
                if q in block:
                    # Store the RAW quantity (e.g. "2Gi") for a readable diff; comparison
                    # normalizes on the fly (see _diff_projections) so "2Gi"=="2048Mi".
                    proj[f"{base}.resources.{rk}.{q}"] = block.get(q)
    return proj


_ABSENT = "<absent>"


def _norm_for_compare(path: str, value):
    """Normalize a projected value for drift comparison only (cpu→millicores,
    memory→bytes) so equal-but-differently-formatted quantities don't read as drift.
    The raw value is what gets displayed."""
    if value is _ABSENT or value is None:
        return value
    if path.endswith(".cpu"):
        return _norm_quantity("cpu", value)
    if path.endswith(".memory"):
        return _norm_quantity("memory", value)
    return value


def _humanize_mem_display(v):
    """Render a memory value readably for the drift detail: plain byte counts become
    Gi/Mi/Ki; already-suffixed values (e.g. '2Gi') are left as-is."""
    try:
        n = int(str(v))
    except (TypeError, ValueError):
        return v
    for unit, mult in (("Gi", 1024 ** 3), ("Mi", 1024 ** 2), ("Ki", 1024)):
        if n >= mult and n % mult == 0:
            return f"{n // mult}{unit}"
    return str(n)


def _display_value(path: str, value):
    """Make a projected value safe + readable for the drift detail. Env values are never
    shown (may be secrets) — only whether the value is set/absent; the env KEY stays in
    the path. Memory is humanized; everything else is shown as-is."""
    if ".env[" in path:
        return "(absent)" if value is _ABSENT else "set (value hidden)"
    if value is _ABSENT:
        return "(absent)"
    if path.endswith(".memory"):
        return _humanize_mem_display(value)
    return value


def _diff_projections(helm_proj: Dict, live_proj: Dict) -> Optional[List[Dict]]:
    """Diff two patchable-field projections → [{path, helm, live}] for differing fields.
    Comparison is normalized (quantities); the displayed values are raw + readable, with
    env values redacted (the env KEY remains visible in the path)."""
    detail = []
    for key in sorted(set(helm_proj) | set(live_proj)):
        hv = helm_proj.get(key, _ABSENT)
        lv = live_proj.get(key, _ABSENT)
        if _norm_for_compare(key, hv) != _norm_for_compare(key, lv):
            detail.append({"path": key,
                           "helm": _display_value(key, hv),
                           "live": _display_value(key, lv)})
    return detail or None


# =============================================================================
# Resource-specific manifest cleaning for comparison
# =============================================================================

# Common metadata fields to exclude from comparison (cluster-specific)
COMMON_EXCLUDE_METADATA = [
    'uid', 'resourceVersion', 'creationTimestamp', 'managedFields',
    'ownerReferences', 'namespace', 'selfLink', 'generation'
]


def _clean_secret_for_comparison(obj: Dict) -> Dict:
    """
    Clean Secret manifest for comparison.
    
    Excludes:
    - Common metadata fields
    - metadata.name (has random suffix, matched via normalization)
    - metadata.finalizers (OpenShift specific)
    - ownerReferences
    - Auto-generated/migration annotations
    - migration/velero labels
    
    Special handling:
    - data: Store key names and per-key hashes (values are sensitive)
      Shows which keys differ, not the actual values
    - .dockercfg key excluded (OpenShift auto-generated)
    """
    import copy
    import hashlib
    
    result = copy.deepcopy(obj)
    
    # Remove status
    result.pop('status', None)
    
    # Clean metadata
    if 'metadata' in result and isinstance(result['metadata'], dict):
        for field in COMMON_EXCLUDE_METADATA:
            result['metadata'].pop(field, None)
        
        # NOTE: Keep metadata.name! It's used as the key in comparison maps.
        # Random suffix normalization is handled separately in compare.py
        
        # Remove finalizers (OpenShift specific, e.g., 'openshift.io/legacy-token')
        result['metadata'].pop('finalizers', None)
        
        # Remove auto-generated/migration/deployment/sensitive annotations
        if 'annotations' in result['metadata'] and isinstance(result['metadata']['annotations'], dict):
            ann_to_remove = [k for k in result['metadata']['annotations'].keys()
                           if k == 'kubectl.kubernetes.io/last-applied-configuration'
                           or k.startswith('openshift.io/migration')
                           or k.startswith('openshift.io/token-secret')  # Contains sensitive token data
                           or k.startswith('openshift.io/internal-registry-auth-token')  # Registry tokens
                           or k.startswith('kubernetes.io/service-account')  # Service account metadata
                           or k.startswith('kubernetes.io/created-by')  # Auto-generated by OpenShift
                           or (_EXTRA_ANN and k.startswith(_EXTRA_ANN))]
            for key in ann_to_remove:
                del result['metadata']['annotations'][key]
            if not result['metadata']['annotations']:
                del result['metadata']['annotations']
        
        # Remove migration/velero/legacy-token labels
        if 'labels' in result['metadata'] and isinstance(result['metadata']['labels'], dict):
            labels_to_remove = [k for k in result['metadata']['labels'].keys() 
                               if k.startswith('migration.openshift.io/')
                               or k.startswith('velero.io/')
                               or k.startswith('kubernetes.io/legacy-token')  # Legacy token labels
                               or k.startswith('openshift.io/legacy-token')]  # Legacy token labels
            for key in labels_to_remove:
                del result['metadata']['labels'][key]
            if not result['metadata']['labels']:
                del result['metadata']['labels']
    
    # For data: keep keys but hash each value separately
    # This allows us to show which specific keys differ
    # Skip OpenShift auto-generated keys like .dockercfg
    if 'data' in result and isinstance(result['data'], dict):
        hashed_data = {}
        for key, value in result['data'].items():
            # Skip OpenShift auto-generated keys
            if key == '.dockercfg' or key == '.dockerconfigjson':
                continue
            # Hash individual value
            value_hash = hashlib.sha256(str(value).encode()).hexdigest()[:12]
            hashed_data[key] = value_hash
        result['_data'] = hashed_data  # Keys with hashed values
        del result['data']
    
    # Same for stringData if present
    if 'stringData' in result:
        del result['stringData']
    
    return result


def _clean_configmap_for_comparison(obj: Dict) -> Dict:
    """
    Clean ConfigMap manifest for comparison.
    
    Excludes:
    - Common metadata fields
    - ownerReferences
    - Auto-generated/migration annotations
    - migration/velero labels
    
    Special handling:
    - data: Store key names and per-key hashes (values may be sensitive)
      Shows which keys differ, not the actual values
    """
    import copy
    import hashlib
    
    result = copy.deepcopy(obj)
    
    # Remove status
    result.pop('status', None)
    
    # Clean metadata
    if 'metadata' in result and isinstance(result['metadata'], dict):
        for field in COMMON_EXCLUDE_METADATA:
            result['metadata'].pop(field, None)
        
        # Remove auto-generated/migration annotations
        if 'annotations' in result['metadata'] and isinstance(result['metadata']['annotations'], dict):
            ann_to_remove = [k for k in result['metadata']['annotations'].keys()
                           if k == 'kubectl.kubernetes.io/last-applied-configuration'
                           or k.startswith('openshift.io/migration')
                           or (_EXTRA_ANN and k.startswith(_EXTRA_ANN))]
            for key in ann_to_remove:
                del result['metadata']['annotations'][key]
            if not result['metadata']['annotations']:
                del result['metadata']['annotations']
        
        # Remove migration/velero labels
        if 'labels' in result['metadata'] and isinstance(result['metadata']['labels'], dict):
            labels_to_remove = [k for k in result['metadata']['labels'].keys() 
                               if k.startswith('migration.openshift.io/') or k.startswith('velero.io/')]
            for key in labels_to_remove:
                del result['metadata']['labels'][key]
            if not result['metadata']['labels']:
                del result['metadata']['labels']
    
    # For data: keep keys but hash each value separately
    if 'data' in result and isinstance(result['data'], dict):
        hashed_data = {}
        for key, value in result['data'].items():
            value_hash = hashlib.sha256(str(value).encode()).hexdigest()[:12]
            hashed_data[key] = value_hash
        result['_data'] = hashed_data
        del result['data']
    
    # Same for binaryData if present
    if 'binaryData' in result and isinstance(result['binaryData'], dict):
        hashed_binary = {}
        for key, value in result['binaryData'].items():
            value_hash = hashlib.sha256(str(value).encode()).hexdigest()[:12]
            hashed_binary[key] = value_hash
        result['_binaryData'] = hashed_binary
        del result['binaryData']
    
    return result


def _clean_pvc_for_comparison(obj: Dict) -> Dict:
    """
    Clean PVC manifest for comparison.
    
    Excludes:
    - Common metadata fields
    - metadata.finalizers
    - metadata.labels with elasticsearch/velero patterns
    - Auto-generated annotations (kubectl, pv.kubernetes.io, volume.kubernetes.io)
    - spec.volumeName (random part)
    - status (entire section)
    """
    import copy
    
    result = copy.deepcopy(obj)
    
    # Remove status entirely
    result.pop('status', None)
    
    # Clean metadata
    if 'metadata' in result and isinstance(result['metadata'], dict):
        meta = result['metadata']
        
        # Remove common fields
        for field in COMMON_EXCLUDE_METADATA:
            meta.pop(field, None)
        
        # Remove finalizers
        meta.pop('finalizers', None)
        
        # Remove auto-generated annotations
        if 'annotations' in meta and isinstance(meta['annotations'], dict):
            annotations_to_remove = []
            for ann_key in meta['annotations'].keys():
                # kubectl auto-generated
                if ann_key == 'kubectl.kubernetes.io/last-applied-configuration':
                    annotations_to_remove.append(ann_key)
                # PV controller auto-generated
                elif ann_key.startswith('pv.kubernetes.io/'):
                    annotations_to_remove.append(ann_key)
                # Volume provisioner auto-generated
                elif ann_key.startswith('volume.kubernetes.io/'):
                    annotations_to_remove.append(ann_key)
                elif ann_key.startswith('volume.beta.kubernetes.io/'):
                    annotations_to_remove.append(ann_key)
                # Migration-related
                elif ann_key.startswith('openshift.io/migration'):
                    annotations_to_remove.append(ann_key)
            
            for key in annotations_to_remove:
                del meta['annotations'][key]
            
            if not meta['annotations']:
                del meta['annotations']
        
        # Remove cluster-specific labels
        if 'labels' in meta and isinstance(meta['labels'], dict):
            labels_to_remove = []
            for label_key, label_value in list(meta['labels'].items()):
                # Remove elasticsearch-related labels
                if label_key.startswith('elasticsearch.k8s.elastic.co/'):
                    labels_to_remove.append(label_key)
                elif label_key.startswith('common.k8s.elastic.co/'):
                    labels_to_remove.append(label_key)
                # Remove velero/migration labels
                elif label_key.startswith('migration.openshift.io/'):
                    labels_to_remove.append(label_key)
                elif label_key.startswith('velero.io/'):
                    labels_to_remove.append(label_key)
                # Remove Kasten backup labels (cluster-specific)
                elif label_key == 'kastennfs':
                    labels_to_remove.append(label_key)
                # Remove direct volume migration labels (MTC migration)
                elif label_key == 'directvolumemigration':
                    labels_to_remove.append(label_key)
                # Remove app.kubernetes.io/part-of if value is openshift-migration
                elif label_key == 'app.kubernetes.io/part-of' and label_value == 'openshift-migration':
                    labels_to_remove.append(label_key)
            
            for key in labels_to_remove:
                del meta['labels'][key]
            
            if not meta['labels']:
                del meta['labels']
    
    # Clean spec.volumeName - normalize the random suffix
    # Pattern: pvc-<random-uuid> -> pvc-
    if 'spec' in result and isinstance(result['spec'], dict):
        if 'volumeName' in result['spec']:
            vol_name = result['spec']['volumeName']
            if vol_name and vol_name.startswith('pvc-'):
                # Keep only the prefix for comparison
                result['spec']['volumeName'] = 'pvc-<normalized>'
    
    return result


def _clean_route_for_comparison(obj: Dict) -> Dict:
    """
    Clean Route manifest for comparison.
    
    Excludes:
    - Common metadata fields
    - metadata.name (route names have random suffixes, matched separately)
    - metadata.labels with migration/velero patterns
    - Auto-generated/migration/deployment annotations
    - status (entire section)
    
    Special handling:
    - spec.tls.key and spec.tls.certificate: Hash values (sensitive content)
    
    Note: Route name normalization (random suffix) is handled separately
    during comparison matching, not here.
    """
    import copy
    import hashlib
    
    result = copy.deepcopy(obj)
    
    # Remove status entirely
    result.pop('status', None)
    
    # Clean metadata
    if 'metadata' in result and isinstance(result['metadata'], dict):
        meta = result['metadata']
        
        # Remove common fields
        for field in COMMON_EXCLUDE_METADATA:
            meta.pop(field, None)
        
        # NOTE: Keep metadata.name! It's used as the key in comparison maps.
        # Random suffix normalization is handled separately in compare.py
        
        # Remove auto-generated/migration/deployment annotations
        if 'annotations' in meta and isinstance(meta['annotations'], dict):
            ann_to_remove = [k for k in meta['annotations'].keys()
                           if k == 'kubectl.kubernetes.io/last-applied-configuration'
                           or k.startswith('openshift.io/migration')
                           or (_EXTRA_ANN and k.startswith(_EXTRA_ANN))]
            for key in ann_to_remove:
                del meta['annotations'][key]
            if not meta['annotations']:
                del meta['annotations']
        
        # Remove cluster-specific/migration labels
        if 'labels' in meta and isinstance(meta['labels'], dict):
            labels_to_remove = []
            for label_key in meta['labels'].keys():
                # Remove migration/velero labels
                if label_key.startswith('migration.openshift.io/'):
                    labels_to_remove.append(label_key)
                elif label_key.startswith('velero.io/'):
                    labels_to_remove.append(label_key)
            
            for key in labels_to_remove:
                del meta['labels'][key]
            
            if not meta['labels']:
                del meta['labels']
    
    # Handle TLS section - hash sensitive fields
    if 'spec' in result and isinstance(result['spec'], dict):
        spec = result['spec']
        if 'tls' in spec and isinstance(spec['tls'], dict):
            tls = spec['tls']
            
            # Hash private key (very sensitive)
            if 'key' in tls and tls['key']:
                key_hash = hashlib.sha256(str(tls['key']).encode()).hexdigest()[:12]
                tls['key'] = f'<hashed:{key_hash}>'
            
            # Hash certificate 
            if 'certificate' in tls and tls['certificate']:
                cert_hash = hashlib.sha256(str(tls['certificate']).encode()).hexdigest()[:12]
                tls['certificate'] = f'<hashed:{cert_hash}>'
            
            # Hash CA certificate if present
            if 'caCertificate' in tls and tls['caCertificate']:
                ca_hash = hashlib.sha256(str(tls['caCertificate']).encode()).hexdigest()[:12]
                tls['caCertificate'] = f'<hashed:{ca_hash}>'
            
            # Hash destination CA certificate if present
            if 'destinationCACertificate' in tls and tls['destinationCACertificate']:
                dest_ca_hash = hashlib.sha256(str(tls['destinationCACertificate']).encode()).hexdigest()[:12]
                tls['destinationCACertificate'] = f'<hashed:{dest_ca_hash}>'
    
    return result


def _clean_service_for_comparison(obj: Dict) -> Dict:
    """
    Clean Service manifest for comparison.
    
    Excludes:
    - Common metadata fields
    - Auto-generated/migration/deployment annotations
    - migration/velero labels
    - spec.clusterIP, spec.clusterIPs (cluster-specific)
    - status
    """
    import copy
    
    result = copy.deepcopy(obj)
    
    # Remove status entirely
    result.pop('status', None)
    
    # Clean metadata
    if 'metadata' in result and isinstance(result['metadata'], dict):
        for field in COMMON_EXCLUDE_METADATA:
            result['metadata'].pop(field, None)
        
        # Remove auto-generated/migration/deployment annotations
        if 'annotations' in result['metadata'] and isinstance(result['metadata']['annotations'], dict):
            ann_to_remove = [k for k in result['metadata']['annotations'].keys()
                           if k == 'kubectl.kubernetes.io/last-applied-configuration'
                           or k.startswith('openshift.io/migration')
                           or (_EXTRA_ANN and k.startswith(_EXTRA_ANN))]
            for key in ann_to_remove:
                del result['metadata']['annotations'][key]
            if not result['metadata']['annotations']:
                del result['metadata']['annotations']
        
        # Remove migration/velero labels
        if 'labels' in result['metadata'] and isinstance(result['metadata']['labels'], dict):
            labels_to_remove = [k for k in result['metadata']['labels'].keys() 
                               if k.startswith('migration.openshift.io/') or k.startswith('velero.io/')]
            for key in labels_to_remove:
                del result['metadata']['labels'][key]
            if not result['metadata']['labels']:
                del result['metadata']['labels']
    
    # Remove cluster-specific spec fields
    if 'spec' in result and isinstance(result['spec'], dict):
        result['spec'].pop('clusterIP', None)
        result['spec'].pop('clusterIPs', None)
    
    return result


def _image_from_pod_spec(pod_spec) -> Optional[str]:
    if not pod_spec or not pod_spec.containers:
        return None
    # Prefer first app container
    return pod_spec.containers[0].image if pod_spec.containers else None


def _images_from_pod_spec(pod_spec) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    try:
        containers = getattr(pod_spec, "containers", None) or []
        for c in containers:
            img = getattr(c, "image", None)
            if img:
                results.append({
                    "name": getattr(c, "name", ""),
                    "image": img,
                })
    except Exception:
        pass
    return results
