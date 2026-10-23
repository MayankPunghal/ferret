# Blind review sheet (summaries shuffled per page; key kept outside the repository)


## authentication-mechanisms.md

### Excerpt given to the models

# Hardening Guide - Authentication Mechanisms

Selecting the appropriate authentication mechanism(s) is a crucial aspect of securing your cluster.
Kubernetes provides several built-in mechanisms, each with its own strengths and weaknesses that
should be carefully considered when choosing the best authentication mechanism for your cluster.

In general, it is recommended to enable as few authentication mechanisms as possible to simplify
user management and prevent cases where users retain access to a cluster that is no longer required.

It is important to note that Kubernetes does not have an in-built user database within the cluster.
Instead, it takes user information from the configured authentication system and uses that to make
authorization decisions. Therefore, to audit user access, you need to review credentials from every
configured authentication source.

For production clusters with multiple users directly accessing the Kubernetes API, it is
recommended to use external authentication sources such as OIDC. The internal authentication
mechanisms, such as client certificates and service account tokens, described below, are not
suitable for this use case.

## X.509 client certificate authentication {#x509-client-certificate-authentication}

Kubernetes leverages [X.509 client certificate](/docs/reference/access-authn-authz/authentication/#x509-client-certificates)
authentication for system components, such as when the kubelet authenticates to the API Server.
While this mechanism can also be used for user authentication, it might not be suitable for
production use due to several restrictions:

- Client certificates cannot be individually revoked. Once compromised, a certificate can be used
  by an attacker until it expires. To mitigate this risk, it is recommended to configure short
  lifetimes for user authentication credentials created using client certificates.
- If a certificate needs to be invalidated, the certificate authority must be re-keyed, which
  can introduce availability risks to the cluster.
- There is no permanent record of client certificates created in the cluster. Therefore, all
  issued certificates must be recorded if you need to keep track of them.
- Private keys used for client certificate authentication cannot be password-protected. Anyone
  who can read the file containing the key will be able to make use of it.
- Using client certificate authentication requires a direct connection from the client to the
  API server without any intervening TLS termination points, which can complicate network architectures.
- Group data is embedded in the `O` value of the client certificate, which means the user's group
  memberships cannot be changed for the lifetime of the certificate.

## Static token file {#static-token-file}

Although Kubernetes allows you to load credentials from a
[static token file](/docs/reference/access-authn-authz/authentication/#static-token-file) located
on the control plane node disks, this approach is not

### Summaries

- **A**: This guide advises on selecting appropriate authentication mechanisms for Kubernetes clusters. It discusses the pros and cons of various built-in methods to help secure user access effectively.  Topics: X.509 client certificate authentication; Static token file; Bootstrap tokens; ServiceAccount secret tokens; TokenRequest API tokens; OpenID Connect token authentication
- **B**: This guide details various authentication mechanisms available in Kubernetes, such as X.509 certificates and various token types. It advises on selecting appropriate methods for production environments, recommending external sources like OIDC for multi-user access.  Topics: X.509 client certificate authentication; OpenID Connect; ServiceAccount tokens; Static token file; TokenRequest API tokens; Kubernetes API Server
- **C**: This document outlines Kubernetes authentication mechanisms, including X.509 client certificates, static token files, and service account tokens. It explains the security implications, limitations, and best practices for each method to ensure cluster security.  Topics: X.509 client certificate authentication; Static token file; Bootstrap tokens; ServiceAccount secret tokens; TokenRequest API tokens; OpenID Connect token authentication
- **D**: This guide explains Kubernetes' built-in authentication mechanisms including X.509 client certificates, static token files, and bootstrap tokens. It warns against using these for production clusters due to certificate revocation risks, lack of permanent records, and password-protected keys that expose secrets.  Topics: X.509 client certificate authentication; Static token file; Bootstrap tokens; ServiceAccount secret tokens; TokenRequest API tokens; OpenID Connect token authentication
- **E**: This document outlines Kubernetes authentication mechanisms and security best practices, recommending minimal mechanisms and external sources like OIDC for production. It details risks and limitations of internal methods such as X.509 certificates and static token files.  Topics: X.509 client certificates; Static token files; Bootstrap tokens; ServiceAccount secret tokens; TokenRequest API; OpenID Connect

## cluster-administration.md

### Excerpt given to the models

# Cluster Administration

The cluster administration overview is for anyone creating or administering a Kubernetes cluster.
It assumes some familiarity with core Kubernetes [concepts](/docs/concepts/).

## Planning a cluster

See the guides in [Setup](/docs/setup/) for examples of how to plan, set up, and configure
Kubernetes clusters. The solutions listed in this article are called *distros*.

Note: 
Not all distros are actively maintained. Choose distros which have been tested with a recent
version of Kubernetes.

Before choosing a guide, here are some considerations:

- Do you want to try out Kubernetes on your computer, or do you want to build a high-availability,
  multi-node cluster? Choose distros best suited for your needs.
- Will you be using **a hosted Kubernetes cluster**, such as
  [Google Kubernetes Engine](https://cloud.google.com/kubernetes-engine/), or **hosting your own cluster**?
- Will your cluster be **on-premises**, or **in the cloud (IaaS)**? Kubernetes does not directly
  support hybrid clusters. Instead, you can set up multiple clusters.
- **If you are configuring Kubernetes on-premises**, consider which
  [networking model](/docs/concepts/cluster-administration/networking/) fits best.
- Will you be running Kubernetes on **"bare metal" hardware** or on **virtual machines (VMs)**?
- Do you **want to run a cluster**, or do you expect to do **active development of Kubernetes project code**?
  If the latter, choose an actively-developed distro. Some distros only use binary releases, but
  offer a greater variety of choices.
- Familiarize yourself with the [components](/docs/concepts/overview/components/) needed to run a cluster.

## Managing a cluster

* Learn how to [manage nodes](/docs/concepts/architecture/nodes/).
  * Read about [Node autoscaling](/docs/concepts/cluster-administration/node-autoscaling/).

* Learn how to set up and manage the [resource quota](/docs/concepts/policy/resource-quotas/) for shared clusters.

## Securing a cluster

* [Generate Certificates](/docs/tasks/administer-cluster/certificates/) describes the steps to
  generate certificates using different tool chains.

* [Kubernetes Container Environment](/docs/concepts/containers/container-environment/) describes
  the environment for Kubelet managed containers on a Kubernetes node.

* [Controlling Access to the Kubernetes API](/docs/concepts/security/controlling-access) describes
  how Kubernetes implements access control for its own API.

* [Authenticating](/docs/reference/access-authn-authz/authentication/) explains authentication in
  Kubernetes, including the various authentication options.

* [Authorization](/docs/reference/access-authn-authz/authorization/) is separate from
  authentication, and controls how HTTP calls are handled.

* [Using Admission Controllers](/docs/reference/access-authn-authz/admission-controllers/)
  explains plug-ins which intercepts requests to the Kubernetes API server after authentication
  and authorization.

* [Admis

### Summaries

- **A**: This guide covers planning, managing, and securing Kubernetes clusters, including node management, resource quotas, and security features like certificates and admission controllers.  Topics: Kubernetes; Nodes; Resource Quotas; Security; Admission Controllers; Networking
- **B**: This guide covers the planning, management, and security of Kubernetes clusters, including distro selection, node administration, and access control. It provides essential resources for administrators to build and secure production-ready environments.  Topics: Kubernetes; Cluster Administration; Node Management; Security; Certificates; Distros
- **C**: This document provides an overview for Kubernetes administrators covering cluster planning, node management, resource quotas, and security configurations including certificate generation and API access control.  Topics: Kubernetes; Kubelet; Nodes; Resource Quotas; Certificates; API Access Control
- **D**: This document provides an overview for creating and administering Kubernetes clusters. It covers planning, managing, and securing clusters, including details on networking, access control, and node management.  Topics: Kubernetes; Cluster Planning; Cluster Management; Cluster Security; Kubelet; Networking
- **E**: This document guides users on planning, managing, and securing Kubernetes clusters. It covers considerations for setup, node management, and various security mechanisms.  Topics: Kubernetes concepts; Distros; Node autoscaling; Resource quota; Certificates; Kubelet

## autoscaling.md

### Excerpt given to the models

# Autoscaling Workloads

In Kubernetes, you can _scale_ a workload depending on the current demand of resources.
This allows your cluster to react to changes in resource demand more elastically and efficiently.

When you scale a workload, you can either increase or decrease the number of replicas managed by
the workload, or adjust the resources available to the replicas in-place.

The first approach is referred to as _horizontal scaling_, while the second is referred to as
_vertical scaling_.

There are manual and automatic ways to scale your workloads, depending on your use case.

## Scaling workloads manually

Kubernetes supports _manual scaling_ of workloads. Horizontal scaling can be done
using the `kubectl` CLI.
For vertical scaling, you need to _patch_ the resource definition of your workload.

See below for examples of both strategies.

- **Horizontal scaling**: [Running multiple instances of your app](/docs/tutorials/kubernetes-basics/scale/scale-intro/)
- **Vertical scaling**: [Resizing CPU and memory resources assigned to containers](/docs/tasks/configure-pod-container/resize-container-resources)

## Scaling workloads automatically

Kubernetes also supports _automatic scaling_ of workloads, which is the focus of this page.

The concept of _Autoscaling_ in Kubernetes refers to the ability to automatically update an
object that manages a set of Pods (for example a
Deployment).

### Scaling workloads horizontally

In Kubernetes, you can automatically scale a workload horizontally using a [HorizontalPodAutoscaler](/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/) (HPA).

It is implemented as a Kubernetes API resource and a controller
and periodically adjusts the number of replicas
in a workload to match observed resource utilization such as CPU or memory usage.

There is a [walkthrough tutorial](/docs/tasks/run-application/horizontal-pod-autoscale-walkthrough) of configuring a HorizontalPodAutoscaler for a Deployment.

### Scaling workloads vertically

You can automatically scale a workload vertically using a [VerticalPodAutoscaler](/docs/concepts/workloads/autoscaling/vertical-pod-autoscale/) (VPA).
Unlike the HPA, the VPA doesn't come with Kubernetes by default, but is a an add-on that you or a cluster administrator may need to deploy before you can use it.

Once installed, it allows you to create CustomResourceDefinitions
(CRDs) for your workloads which define _how_ and _when_ to scale the resources of the managed replicas.

Note: 
You will need to have the [Metrics Server](https://github.com/kubernetes-sigs/metrics-server)
installed to your cluster for the VPA to work.

#### In-place pod vertical scaling

As of Kubernetes , VPA does not support resizing pods in-place,
but this integration is being worked on.
For manually resizing pods in-place, see [Resize Container Resources In-Place](/docs/tasks/configure-pod-container/resize-container-resources/).

### Autoscaling based on cluster size

For workloads that need to be sca

### Summaries

- **A**: This document explains how to scale Kubernetes workloads manually or automatically using horizontal and vertical strategies. It covers tools like HPA and VPA, requirements such as the Metrics Server, and limitations regarding in-place resizing.  Topics: Kubernetes; HorizontalPodAutoscaler; VerticalPodAutoscaler; Metrics Server; Pods; Deployments
- **B**: This document explains how Kubernetes manages workload scaling through manual and automatic methods. It details horizontal scaling via HorizontalPodAutoscalers and vertical scaling via VerticalPodAutoscalers, including in-place pod resizing and cluster size-based configurations.  Topics: HorizontalPodAutoscaler; VerticalPodAutoscaler; Deployment; Metrics Server; Horizontal Scaling; Vertical Scaling
- **C**: This document explains how to scale Kubernetes workloads manually (horizontal/vertical) and automatically using HorizontalPodAutoscaler (HPA) and VerticalPodAutoscaler (VPA) based on cluster size or schedules.  Topics: Kubernetes; HorizontalPodAutoscaler (HPA); VerticalPodAutoscaler (VPA); Cluster Size; Workload Scaling
- **D**: This document explains how to scale workloads in Kubernetes, covering manual scaling, horizontal scaling using HPA, and vertical scaling using VPA. It details various methods for adapting workloads to resource demand.  Topics: HorizontalPodAutoscaler (HPA); VerticalPodAutoscaler (VPA); Manual scaling; Scaling workloads horizontally; Scaling workloads vertically; Metrics Server
- **E**: This document explains how to scale workloads in Kubernetes, covering both manual and automatic scaling methods. It details horizontal and vertical scaling, including the use of HPA and VPA.  Topics: Kubernetes; HorizontalPodAutoscaler (HPA); VerticalPodAutoscaler (VPA); Horizontal Scaling; Vertical Scaling; Metrics Server

## api-server-bypass-risks.md

### Excerpt given to the models

# Kubernetes API Server Bypass Risks

The Kubernetes API server is the main point of entry to a cluster for external parties
(users and services) interacting with it.

As part of this role, the API server has several key built-in security controls, such as
audit logging and admission controllers.
However, there are ways to modify the configuration
or content of the cluster that bypass these controls.

This page describes the ways in which the security controls built into the
Kubernetes API server can be bypassed, so that cluster operators
and security architects can ensure that these bypasses are appropriately restricted.

## Static Pods {#static-pods}

The kubelet on each node loads and
directly manages any manifests that are stored in a named directory or fetched from
a specific URL as [*static Pods*](/docs/tasks/configure-pod-container/static-pod) in
your cluster. The API server doesn't manage these static Pods. An attacker with write
access to this location could modify the configuration of static pods loaded from that
source, or could introduce new static Pods.

Static Pods are restricted from accessing other objects in the Kubernetes API. For example,
you can't configure a static Pod to mount a Secret from the cluster. However, these Pods can
take other security sensitive actions, such as using `hostPath` mounts from the underlying
node.

By default, the kubelet creates a mirror pod
so that the static Pods are visible in the Kubernetes API. However, if the attacker uses an invalid
namespace name when creating the Pod, it will not be visible in the Kubernetes API and can only
be discovered by tooling that has access to the affected host(s).

If a static Pod fails admission control, the kubelet won't register the Pod with the
API server. However, the Pod still runs on the node. For more information, refer to
[kubeadm issue #1541](https://github.com/kubernetes/kubeadm/issues/1541#issuecomment-487331701).

### Mitigations {#static-pods-mitigations}

- Only [enable the kubelet static Pod manifest functionality](/docs/tasks/configure-pod-container/static-pod/#static-pod-creation)
  if required by the node.
- If a node uses the static Pod functionality, restrict filesystem access to the static Pod manifest directory
  or URL to users who need the access.
- Restrict access to kubelet configuration parameters and files to prevent an attacker setting
  a static Pod path or URL.
- Regularly audit and centrally report all access to directories or web storage locations that host
  static Pod manifests and kubelet configuration files.

## The kubelet API {#kubelet-api}

The kubelet provides an HTTP API that is typically exposed on TCP port 10250 on cluster
worker nodes. The API might also be exposed on control plane nodes depending on the Kubernetes
distribution in use. Direct access to the API allows for disclosure of information about
the pods running on a node, the logs from those pods, and execution of commands in
every container running on the node.

### Summaries

- **A**: This document details various methods by which security controls within the Kubernetes API server can be bypassed. It focuses on risks associated with Static Pods, the kubelet API, and the etcd API.  Topics: Kubernetes API Server; Static Pods; kubelet API; etcd API; Container runtime socket
- **B**: This document describes ways security controls in the Kubernetes API server can be bypassed, focusing on static Pods and the kubelet API. It guides operators on restricting these bypasses to maintain cluster security.  Topics: Kubernetes API Server; Static Pods; kubelet API; etcd API; Container runtime socket
- **C**: This document details security vulnerabilities in the Kubernetes API server, specifically focusing on bypasses via Static Pods, the kubelet API, and etcd. It explains how attackers can circumvent built-in controls and provides mitigation strategies to restrict these risks.  Topics: Kubernetes API Server; Static Pods; kubelet API; etcd API; Container runtime socket; Admission Controllers
- **D**: This document outlines security vulnerabilities where attackers can bypass Kubernetes API server controls, including static pod manipulation and direct kubelet API access. It details specific bypass mechanisms and provides mitigation strategies for cluster operators and security architects.  Topics: Kubernetes API Server; Static Pods; kubelet API; etcd API; Container Runtime; Admission Controllers
- **E**: { "title": "Kubernetes API Server Bypass Risks", "summary": "This document outlines the security risks and mitigations for bypassing built-in security controls of the Kubernetes API server, including static pods, kubelet API, etcd API, and runtime socket configurations.", "topics": [ "Kubernetes API Server", "Static Pods", "Kubelet API", "Kubelet Static Pod Manifest", "Kubelet API", "Kubelet API",

## cloud-native-security.md

### Excerpt given to the models

# Cloud Native Security and Kubernetes

Kubernetes is based on a cloud native architecture and draws on advice from the
CNCF about good practices for
cloud native information security.

Read on for an overview of how Kubernetes is designed to help you deploy a
secure cloud native platform.

## Cloud native information security

There are localized versions available of this whitepaper; if you can link to one of those
when localizing, that's even better.

The CNCF [white paper](https://github.com/cncf/tag-security/blob/main/community/resources/security-whitepaper/v2/CNCF_cloud-native-security-whitepaper-May2022-v2.pdf)
on cloud native security defines security controls and practices that are
appropriate to different _lifecycle phases_.

## _Develop_ lifecycle phase {#lifecycle-phase-develop}

- Ensure the integrity of development environments.
- Design applications following good practices for information security,
  appropriate for your context.
- Consider end user security as part of solution design.

To achieve this, you can:

1. Adopt an architecture, such as [zero trust](https://glossary.cncf.io/zero-trust-architecture/),
   that minimizes attack surfaces, even for internal threats.
1. Define a code review process that considers security concerns.
1. Build a _threat model_ of your system or application that identifies
   trust boundaries. Use that threat model to identify risks and determine
   how to treat them.
1. Incorporate advanced security automation, such as _fuzzing_ and
   [security chaos engineering](https://glossary.cncf.io/security-chaos-engineering/),
   where it's justified.

## _Distribute_ lifecycle phase {#lifecycle-phase-distribute}

- Ensure the security of the supply chain for container images you execute.
- Ensure the security of the supply chain for the cluster and other components
  that execute your application. For example, this might include an external
  database that your cloud native application uses for persistence.

To achieve this, you can:

1. Scan container images and other artifacts for known vulnerabilities.
1. Ensure that software distribution uses encryption in transit, with
   a chain of trust for the software source.
1. Adopt and follow processes to update dependencies when updates are
   available, especially in response to security announcements.
1. Use validation mechanisms such as digital certificates for supply
   chain assurance.
1. Subscribe to feeds and other mechanisms to alert you to security
   risks.
1. Restrict access to artifacts. Place container images in a
   [private registry](/docs/concepts/containers/images/#using-a-private-registry)
   that only allows authorized clients to pull images.

## _Deploy_ lifecycle phase {#lifecycle-phase-deploy}

Ensure appropriate restrictions on what can be deployed, who can deploy it,
and where it can be deployed.
You can enforce measures from the _distribute_ phase, such as verifying the
cryptographic identity of container image artifacts.

You can de

### Summaries

- **A**: This document provides an overview of cloud native information security practices, specifically within the context of Kubernetes. It details security controls across various lifecycle phases, including development, distribution, deployment, and runtime.  Topics: Kubernetes; Cloud Native Security; Lifecycle Phases; Runtime Protection; Supply Chain Security; Zero Trust
- **B**: This document outlines the lifecycle phases for securing cloud-native platforms, including development, distribution, and deployment. It details security controls for each phase, such as zero trust architecture, supply chain security for container images, and runtime protection for compute and storage resources.  Topics: Kubernetes; Cloud Native Security; Zero Trust Architecture; Supply Chain Security; Runtime Protection
- **C**: This document outlines CNCF best practices for securing cloud native applications across the develop, distribute, deploy, and runtime phases. It details specific security controls, such as threat modeling, supply chain scanning, and access restrictions, to ensure a secure Kubernetes environment.  Topics: Kubernetes; Cloud Native Security; CNCF; Zero Trust; Supply Chain Security; Threat Modeling
- **D**: This document outlines cloud-native information security practices across the development, distribution, deployment, and runtime lifecycle phases. It provides guidance on securing applications and the software supply chain within a Kubernetes environment.  Topics: Cloud native information security; Develop lifecycle phase; Distribute lifecycle phase; Deploy lifecycle phase; Runtime lifecycle phase; Runtime protection: access
- **E**: This document outlines the CNCF's cloud native security framework, detailing how Kubernetes secures the development, distribution, deployment, and runtime phases of cloud-native applications. It emphasizes supply chain protection, threat modeling, and access controls to ensure a secure platform.  Topics: Kubernetes; CNCF; Cloud Native Architecture; Container Security; Zero Trust; Supply Chain Security

## application-security-checklist.md

### Excerpt given to the models

# Application Security Checklist

This checklist aims to provide basic guidelines on securing applications
running in Kubernetes from a developer's perspective.
This list is not meant to be exhaustive and is intended to evolve over time.

On how to read and use this document:

- The order of topics does not reflect an order of priority.
- Some checklist items are detailed in the paragraph below the list of each section.
- This checklist assumes that a `developer` is a Kubernetes cluster user who
  interacts with namespaced scope objects.

Caution: 
Checklists are **not** sufficient for attaining a good security posture on their own.
A good security posture requires constant attention and improvement, but a checklist
can be the first step on the never-ending journey towards security preparedness.
Some recommendations in this checklist may be too restrictive or too lax for
your specific security needs. Since Kubernetes security is not "one size fits all",
each category of checklist items should be evaluated on its merits.

## Base security hardening

The following checklist provides base security hardening recommendations that
would apply to most applications deploying to Kubernetes.

### Application design

- [ ] Follow the right
  [security principles](https://www.cncf.io/wp-content/uploads/2022/06/CNCF_cloud-native-security-whitepaper-May2022-v2.pdf)
  when designing applications.
- [ ] Application configured with appropriate QoS class
  through resource request and limits.
  - [ ] Memory limit is set for the workloads with a limit equal to or greater than the request.
  - [ ] CPU limit might be set on sensitive workloads.

### Service account

- [ ] Avoid using the `default` ServiceAccount. Instead, create ServiceAccounts for
  each workload or microservice.
- [ ] `automountServiceAccountToken` should be set to `false` unless the pod
  specifically requires access to the Kubernetes API to operate.

### Pod-level `securityContext` recommendations {#security-context-pod}

- [ ] Set `runAsNonRoot: true`.
- [ ] Configure the container to execute as a less privileged user
  (for example, using `runAsUser` and `runAsGroup`), and configure appropriate
  permissions on files or directories inside the container image.
- [ ] Optionally add a supplementary group with `fsGroup` to access persistent volumes.
- [ ] The application deploys into a namespace that enforces an appropriate
  [Pod security standard](/docs/concepts/security/pod-security-standards/).
  If you cannot control this enforcement for the cluster(s) where the application is
  deployed, take this into account either through documentation or additional defense in depth.

### Container-level `securityContext` recommendations {#security-context-container}

- [ ] Disable privilege escalations using `allowPrivilegeEscalation: false`.
- [ ] Configure the root filesystem to be read-only with `readOnlyRootFilesystem: true`.
- [ ] Avoid running privileged containers (set `privileged: false`).
- [ ] D

### Summaries

- **A**: This document outlines a developer's guide to securing Kubernetes applications, covering base hardening, service account management, pod-level and container-level security contexts, RBAC, image security, and network policies.  Topics: Kubernetes; Service Account; Security Context; RBAC; Container Security; Pod Security Standards
- **B**: This document provides developer-focused guidelines for securing applications in Kubernetes, covering base hardening, RBAC, and security contexts. It emphasizes non-root execution, read-only filesystems, and least privilege principles to establish a foundational security posture.  Topics: Kubernetes; Security Context; RBAC; Service Accounts; Pod Security Standards; Container Security
- **C**: This checklist provides guidelines for securing applications deployed in Kubernetes from a developer's perspective. It covers various security aspects from application design to container and pod configurations.  Topics: Kubernetes; Application Security; Service Account; Pod Security Context; Container Security Context; RBAC
- **D**: This document provides a developer-focused guide for securing applications running in Kubernetes. It covers base hardening, service accounts, pod and container-level security contexts, RBAC, image security, network policies, and advanced hardening strategies.  Topics: Kubernetes; Service Accounts; Pod Security Standards; Container Security Contexts; Role-Based Access Control (RBAC); Network Policies
- **E**: This document provides a checklist of basic guidelines for securing applications running in Kubernetes from a developer's perspective. It covers areas like application design, service accounts, and container security contexts.  Topics: Application design; Service account; Pod-level `securityContext` recommendations; Container-level `securityContext` recommendations; Role Based Access Control (RBAC); Image security

## admission-webhooks-good-practices.md

### Excerpt given to the models

# Admission Webhook Good Practices

This page provides good practices and considerations when designing
_admission webhooks_ in Kubernetes. This information is intended for
cluster operators who run admission webhook servers or third-party applications
that modify or validate your API requests.

Before reading this page, ensure that you're familiar with the following
concepts:

* [Admission controllers](/docs/reference/access-authn-authz/admission-controllers/)
* [Admission webhooks](/docs/reference/access-authn-authz/extensible-admission-controllers/#what-are-admission-webhooks)

## Importance of good webhook design {#why-good-webhook-design-matters}

Admission control occurs when any create, update, or delete request
is sent to the Kubernetes API. Admission controllers intercept requests that
match specific criteria that you define. These requests are then sent to
mutating admission webhooks or validating admission webhooks. These webhooks are
often written to ensure that specific fields in object specifications exist or
have specific allowed values.

Webhooks are a powerful mechanism to extend the Kubernetes API. Badly-designed
webhooks often result in workload disruptions because of how much control
the webhooks have over objects in the cluster. Like other API extension
mechanisms, webhooks are challenging to test at scale for compatibility with
all of your workloads, other webhooks, add-ons, and plugins. 

Additionally, with every release, Kubernetes adds or modifies the API with new
features, feature promotions to beta or stable status, and deprecations. Even
stable Kubernetes APIs are likely to change. For example, the `Pod` API changed
in v1.29 to add the
[Sidecar containers](/docs/concepts/workloads/pods/sidecar-containers/) feature.
While it's rare for a Kubernetes object to enter a broken state because of a new
Kubernetes API, webhooks that worked as expected with earlier versions of an API
might not be able to reconcile more recent changes to that API. This can result
in unexpected behavior after you upgrade your clusters to newer versions.

This page describes common webhook failure scenarios and how to avoid them by
cautiously and thoughtfully designing and implementing your webhooks. 

## Identify whether you use admission webhooks {#identify-admission-webhooks}

Even if you don't run your own admission webhooks, some third-party applications
that you run in your clusters might use mutating or validating admission
webhooks.

To check whether your cluster has any mutating admission webhooks, run the
following command:

```shell
kubectl get mutatingwebhookconfigurations
```
The output lists any mutating admission controllers in the cluster. 

To check whether your cluster has any validating admission webhooks, run the
following command:

```shell
kubectl get validatingwebhookconfigurations
```
The output lists any validating admission controllers in the cluster. 

## Choose an admission control mechanism {#choose-admission-mechanism}

### Summaries

- **A**: {"title": "Admission Webhook Good Practices", "summary": "This guide outlines critical design principles for Kubernetes admission webhooks, including importance of good design, identifying existing controllers, choosing mechanisms, and ensuring low latency and idempotence to prevent conflicts and failures.", "topics": ["Admission controllers", "Admission webhooks", "Admission control mechanism", "
- **B**: This document outlines essential guidelines for designing Kubernetes admission webhooks to ensure stability, performance, and reliability. It covers critical considerations such as avoiding loops, managing timeouts, and ensuring idempotent mutations.  Topics: Admission controllers; Admission webhooks; Mutating webhooks; Validating webhooks; CustomResourceDefinitions; Kubernetes API
- **C**: This document outlines best practices for designing and implementing admission webhooks in Kubernetes to ensure stability, performance, and compatibility. It covers critical considerations such as avoiding loops, ensuring idempotence, managing mutations, and validating final states.  Topics: Admission Controllers; Mutating Webhooks; Validating Webhooks; CustomResourceDefinitions; Kubernetes API; Cluster Operators
- **D**: This document outlines best practices for designing and implementing admission webhooks in Kubernetes. It covers topics like performance, idempotence, avoiding loops, and ensuring robust deployment for cluster operators.  Topics: Admission Webhooks; Kubernetes API; Admission Controllers; Mutating Webhooks; Validating Webhooks; CustomResourceDefinitions
- **E**: This document outlines best practices for designing and implementing admission webhooks in Kubernetes. It covers considerations for performance, preventing loops, managing mutations, and ensuring compatibility across API version upgrades.  Topics: Admission controllers; Admission webhooks; CustomResourceDefinitions; Performance and latency; Load balancer; High-availability deployment model

## authorization.md

### Excerpt given to the models

# Authorization

Kubernetes authorization takes place following
[authentication](/docs/reference/access-authn-authz/authentication/).
Usually, a client making a request must be authenticated (logged in) before its
request can be allowed; however, Kubernetes also allows anonymous requests in
some circumstances.

For an overview of how authorization fits into the wider context of API access
control, read
[Controlling Access to the Kubernetes API](/docs/concepts/security/controlling-access/).

## Authorization verdicts {#determine-whether-a-request-is-allowed-or-denied}

Kubernetes authorization of API requests takes place within the API server.
The API server evaluates all of the request attributes against all policies,
potentially also consulting external services, and then allows or denies the
request.

All parts of an API request must be allowed by some authorization
mechanism in order to proceed. In other words: access is denied by default.

Note: 
Access controls and policies that
depend on specific fields of specific kinds of objects are handled by
admission controllers.

Kubernetes admission control happens after authorization has completed (and,
therefore, only when the authorization decision was to allow the request).

When multiple [authorization modules](#authorization-modules) are configured,
each is checked in sequence.
If any authorizer _approves_ or _denies_ a request, that decision is immediately
returned and no other authorizer is consulted. If all modules have _no opinion_
on the request, then the request is denied. An overall deny verdict means that
the API server rejects the request and responds with an HTTP 403 (Forbidden)
status.

## Request attributes used in authorization

Kubernetes reviews only the following API request attributes:

 * **user** - The `user` string provided during authentication.
 * **group** - The list of group names to which the authenticated user belongs.
 * **extra** - A map of arbitrary string keys to string values, provided by the authentication layer.
 * **API** - Indicates whether the request is for an API resource.
 * **Request path** - Path to miscellaneous non-resource endpoints like `/api` or `/healthz`.
 * **API request verb** - API verbs like `get`, `list`, `create`, `update`, `patch`, `watch`, `delete`, and `deletecollection` are used for resource requests. To determine the request verb for a resource API endpoint, see [request verbs and authorization](/docs/reference/access-authn-authz/authorization/#determine-the-request-verb).
 * **HTTP request verb** - Lowercased HTTP methods like `get`, `post`, `put`, and `delete` are used for non-resource requests.
 * **Resource** - The ID or name of the resource that is being accessed (for resource requests only) -- For resource requests using `get`, `update`, `patch`, and `delete` verbs, you must provide the resource name.
 * **Subresource** - The subresource that is being accessed (for resource requests only). This can be a standard subresource (for 

### Summaries

- **A**: This document explains how Kubernetes determines whether to allow or deny API requests using authorization modules and specific request attributes. It covers configuration methods, privilege escalation risks, and the default deny policy.  Topics: API Server; Authorization Modules; Authentication; Admission Controllers; Privilege Escalation; Authorization Verdicts
- **B**: This document explains how Kubernetes API requests are evaluated for access control, detailing the default deny policy, the sequence of authorization module checks, and the specific request attributes (user, group, API, verb, etc.) used to make authorization decisions.  Topics: Authentication; Admission Controllers; Authorization Modules; API Server; Request Attributes; Privilege Escalation
- **C**: This document explains how Kubernetes authorization works by evaluating request attributes against policies within the API server. Access is denied by default, and decisions are made sequentially by configured authorization modules.  Topics: API server; Authorization verdicts; Request attributes; Authorization modules; Admission controllers
- **D**: This document details how Kubernetes authorizes API requests within the API server. It explains the process of determining whether a request is allowed or denied based on various request attributes and configured authorization modules.  Topics: API Server; Authorization; Request Attributes; Authorization Modules; Authentication; Access Control
- **E**: This document explains how Kubernetes API requests are evaluated for access control, detailing authorization verdicts, request attributes, and modes. It clarifies that requests must pass through admission controllers, with deny decisions made after authorization modules complete their checks.  Topics: authorization; admission controller; authentication; resource requests; non-resource requests

## architecture.md

### Excerpt given to the models

# Cluster Architecture

A Kubernetes cluster consists of a control plane plus a set of worker machines, called nodes,
that run containerized applications. Every cluster needs at least one worker node in order to run Pods.

The worker node(s) host the Pods that are the components of the application workload.
The control plane manages the worker nodes and the Pods in the cluster. In production
environments, the control plane usually runs across multiple computers and a cluster
usually runs multiple nodes, providing fault-tolerance and high availability.

This document outlines the various components you need to have for a complete and working Kubernetes cluster.

The diagram in Figure 1 presents an example reference architecture for a Kubernetes cluster.
The actual distribution of components can vary based on specific cluster setups and requirements.

In the diagram, each node runs the [`kube-proxy`](#kube-proxy) component. You need a
network proxy component on each node to ensure that the
Service API and associated behaviors
are available on your cluster network. However, some network plugins provide their own,
third party implementation of proxying. When you use that kind of network plugin,
the node does not need to run `kube-proxy`.

## Control plane components

The control plane's components make global decisions about the cluster (for example, scheduling),
as well as detecting and responding to cluster events (for example, starting up a new
pod when a Deployment's
`replicas` field is unsatisfied).

Control plane components can be run on any machine in the cluster. However, for simplicity, setup scripts
typically start all control plane components on the same machine, and do not run user containers on this machine.
See [Creating Highly Available clusters with kubeadm](/docs/setup/production-environment/tools/kubeadm/high-availability/)
for an example control plane setup that runs across multiple machines.

### kube-apiserver

### etcd

### kube-scheduler

### kube-controller-manager

There are many different types of controllers. Some examples of them are:

- Node controller: Responsible for noticing and responding when nodes go down.
- Job controller: Watches for Job objects that represent one-off tasks, then creates Pods to run those tasks to completion.
- EndpointSlice controller: Populates EndpointSlice objects (to provide a link between Services and Pods).
- ServiceAccount controller: Create default ServiceAccounts for new namespaces.

The above is not an exhaustive list.

### cloud-controller-manager

The cloud-controller-manager only runs controllers that are specific to your cloud provider.
If you are running Kubernetes on your own premises, or in a learning environment inside your
own PC, the cluster does not have a cloud controller manager.

As with the kube-controller-manager, the cloud-controller-manager combines several logically
independent control loops into a single binary that you run as a single process. You can scale
horizont

### Summaries

- **A**: This document details the essential components of a Kubernetes cluster, including control plane elements like kube-apiserver and etcd, and worker node parts such as kubelet and kube-proxy. It explains how these components interact to manage applications, schedule workloads, and ensure cluster reliability.  Topics: kube-apiserver; etcd; kube-scheduler; kube-controller-manager; kubelet; kube-proxy
- **B**: This document outlines the components required for a complete Kubernetes cluster, including the control plane (kube-apiserver, etcd, scheduler, controller-manager) and worker nodes (kubelet, kube-proxy, cloud-controller-manager). It details how nodes host Pods, manages services, and handles events, while noting that kube-proxy is optional depending on network plugins.  Topics: kube-apiserver; etcd; kube-scheduler; kube-controller-manager; cloud-controller-manager; kubelet
- **C**: This document describes the architecture of a Kubernetes cluster, detailing the roles of the control plane and worker nodes. It explains the function of core components necessary for a complete and operational cluster.  Topics: Control plane; Worker nodes; kube-apiserver; etcd; kube-scheduler; kube-controller-manager
- **D**: This document details the components of a Kubernetes cluster, including the control plane (kube-apiserver, etcd, scheduler, etc.) and worker nodes (kubelet, kube-proxy). It explains how these systems manage workloads, handle network traffic, and ensure high availability across multiple nodes.  Topics: kube-apiserver; etcd; kube-scheduler; kube-controller-manager; cloud-controller-manager; kubelet
- **E**: This document outlines the necessary components for a complete Kubernetes cluster, detailing the roles of the control plane and worker nodes. It explains how these components work together to manage Pods and cluster operations.  Topics: kube-apiserver; etcd; kube-scheduler; kube-controller-manager; cloud-controller-manager; kubelet
