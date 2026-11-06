---
icon: lucide/door-open
hide:
  - toc
---

# Room provisioning

<!-- site-only -->
!!! note "About this page"
    This documents a stack **generated from `soliplex-template`**. A generated
    project ships its own copy of this page without this note.
<!-- endsite-only -->

The room provisioner creates a private RAG room for a requester in one
step: the room's config, its own database, ingestion from the room's
uploads, and access for its owner alone: the requester, unless an admin
names another (see [Rooms for someone else](service.md#rooms-for-someone-else)).

A room is requested in one of two ways:

- a user asks the concierge's about room, whose `request_room` tool calls
  [the provisioner service](service.md) on their behalf
- an operator runs the provisioner
  [from the command line](command-line.md), naming the requester

## A request, end to end

A user asking the concierge's about room for a room, with the
[provisioner service](service.md)'s default settings:

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant A as backend :8000<br/>(agent + concierge tools)
    participant P as soliplex-provisioner<br/>:8090
    participant Z as postgres<br/>(authz)
    participant F as Stack files<br/>src/rooms-provisioned,<br/>uploads/rooms, rag/db
    participant G as Gitea
    participant I as haiku-ingester<br/>:8765

    U->>A: "Make me a room called Q3 plans"
    A->>A: request_room: mint a short-lived service token<br/>with the user's claims (url_safe_token_secret)
    A->>P: POST /rooms {name, slug?, description?}<br/>Bearer service token
    P->>Z: Admission: admin, or WRITE on __provision__?
    alt not admitted
        P-->>A: 403
        A-->>U: Files a "new-room" issue instead
    end
    P->>P: plan_room: owner from preferred_username,<br/>slug from the name -> alice--q3-plans
    P->>A: Check the backend admits the provisioner<br/>(its own service token)
    P->>F: Take the lock, check the room id is free:<br/>room dir, fragment, database, uploads
    P->>Z: Check the room id is free: no policy
    P->>G: File the tracking issue (provision-start)
    P-->>A: 202 {room_id, issue_url}
    A-->>U: "alice--q3-plans is being created"

    Note over P,I: Background: create the room, holding the lock
    P->>F: Write room_config.yaml, REQUEST.md, provision.yaml<br/>to src/rooms-provisioned/alice--q3-plans/
    P->>F: mkdir uploads/rooms/alice--q3-plans
    P->>F: Write databases.d/alice--q3-plans.yaml
    P->>I: Reload the config
    I->>F: Create rag/db/alice--q3-plans.lancedb
    P->>Z: soliplex-cli room-authz: grant the owner, audit the rooms
    P->>F: git commit (the requester as author)
    alt a step before the commit fails
        P->>F: Undo this run's steps, newest first
        P->>G: Report the failure on the issue (provision-failed)
    end
    P->>G: git push the clone
    P->>A: POST /api/v1/installation/reload/rooms:<br/>the room is in the delta
    P->>G: Tag (provision-complete), comment on, and close the issue

    U->>A: "Is it ready?"
    A->>P: GET /rooms/alice--q3-plans
    P-->>A: live / pending / incomplete / failed
    A-->>U: "Ready: refresh your room list"

    U->>A: Upload documents to the room
    A->>F: uploads/rooms/alice--q3-plans/
    I->>F: Its fs source picks up the files
    I->>F: Convert, chunk, embed, store in the room's database
```

## Where to go next

- [The provisioner service](service.md): its endpoints, refusals and
  admission, and rooms for someone else
- [What it does](how-it-works.md): each step, what it refuses before
  changing anything, and its limits
- [From the command line](command-line.md): provisioning a room by hand
- [Reconciling the stack with the clone](reconcile.md): replaying the
  clone's records after the stack drifts from them
- [Configuration](configuration.md): settings, OIDC, git policy and the
  provisioned rooms' agent
