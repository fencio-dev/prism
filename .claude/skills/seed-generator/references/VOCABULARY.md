# Canonical Vocabulary & Labeling Rules

This reference document defines the canonical vocabulary and provides detailed labeling rules for all three classification fields. Use this when assigning labels to training examples.

## Table of Contents

1. [Action Labels](#action-labels)
2. [Resource Type Labels](#resource-type-labels)
3. [Sensitivity Labels](#sensitivity-labels)
4. [Labeling Decision Trees](#labeling-decision-trees)
5. [Edge Cases & Disambiguation](#edge-cases--disambiguation)
6. [Inference Rules](#inference-rules)

---

## Action Labels

The action field describes what operation is being performed on a resource.

### read
**Definition**: Retrieve or access data without modification.

**Keywords**: search, query, get, fetch, list, find, select, retrieve, lookup, show, display, view, browse, scan, seek, examine, inspect, read, load, pull

**Examples**:
- "fetch all users"
- "query the database"
- "list repositories"
- "retrieve customer records"
- "get user by ID"
- "search for items"

**Confidence**: High confidence when you see query/fetch/list/get/retrieve verbs

---

### write
**Definition**: Create new data or insert records.

**Keywords**: create, insert, add, post, put, save, store, write, append, persist, record, submit, register, enroll, publish, upload

**Examples**:
- "create a new user"
- "insert a record into the database"
- "add an item to the cart"
- "save the document"
- "post a comment"
- "upload a file"

**Confidence**: High confidence for create/insert/add/write/save verbs

---

### update
**Definition**: Modify existing data.

**Keywords**: update, modify, change, edit, patch, alter, set, replace, revise, amend, adjust, correct, refine, tweak, enhance

**Examples**:
- "update user profile"
- "modify the configuration"
- "change password"
- "edit document content"
- "patch the issue"
- "adjust settings"

**Important**: `upsert` (create or update) → treat as **write** (creation is primary)

**Confidence**: High confidence for update/modify/change/edit/patch verbs

---

### delete
**Definition**: Remove or delete data.

**Keywords**: delete, remove, drop, destroy, purge, clear, unlink, erase, wipe, discard, eliminate, deprecate, archive (if permanent)

**Examples**:
- "delete user account"
- "remove the record"
- "drop the table"
- "purge old logs"
- "clear cache"
- "destroy session"

**Confidence**: High confidence for delete/remove/drop/purge verbs

---

### execute
**Definition**: Run a function, process, command, or trigger an action without data modification focus.

**Keywords**: execute, run, call, invoke, trigger, start, launch, spawn, activate, initiate, commence, perform, accomplish, complete, fulfill

**Examples**:
- "invoke a webhook"
- "execute a stored procedure"
- "run the pipeline"
- "trigger the workflow"
- "call the service"
- "activate the alarm"

**When to use**: When the primary purpose is execution rather than data access. If execution also involves data modification, prefer the more specific action (read/write/update/delete).

**Confidence**: High confidence for execute/run/call/invoke/trigger verbs

---

### export
**Definition**: Extract data to an external destination (download, backup, dump, transfer out).

**Keywords**: export, download, backup, dump, extract, transfer, ship, send, transmit, deliver, distribute, replicate, copy (when destination is external), sync

**Examples**:
- "export data to CSV"
- "download the report"
- "backup the database"
- "extract records to S3"
- "dump data to file"
- "sync to external system"

**Important**: export = data leaving the system. Distinguish from:
- `copy` within system → read
- `replicate` to backup → export
- `transfer` to external → export

**Confidence**: High confidence for export/download/backup/dump verbs

---

## Resource Type Labels

The resource_type field describes what kind of system or service is being accessed.

### database
**Definition**: Structured data storage systems including SQL databases, NoSQL stores, and data warehouses.

**Keywords**: database, db, sql, nosql, table, collection, record, query, postgres, postgresql, mysql, sqlite, mongodb, dynamodb, rds, cassandra, elasticsearch, bigquery, snowflake, redshift, oracle, mssql

**Examples**:
- "query the users table"
- "insert into mongodb"
- "fetch from postgres"
- "read records from dynamodb"
- "update sql database"

**Inference rules**:
- Tool name contains: db, sql, table, postgres, mongo, dynamodb, cassandra → **database**
- Operation path contains: /queries, /records, /data → likely **database**

---

### storage
**Definition**: File and object storage systems for unstructured data.

**Keywords**: storage, s3, blob, bucket, file, filesystem, object storage, gcs, azure-blob, minio, oss, cos, dropbox, box, gdrive

**Examples**:
- "upload file to S3"
- "read from cloud storage"
- "delete from blob storage"
- "download from Google Cloud Storage"
- "list files in bucket"

**Inference rules**:
- Tool name contains: s3, gcs, blob, bucket, storage, file, archive → **storage**
- Operation path contains: /files, /objects, /buckets → likely **storage**

---

### api
**Definition**: External service endpoints accessed via HTTP/REST/GraphQL.

**Keywords**: api, endpoint, service, http, rest, graphql, webhook, gateway, microservice, web service, third-party service, external service

**Examples**:
- "call the Stripe API"
- "invoke GraphQL endpoint"
- "hit the REST service"
- "trigger webhook"
- "call external microservice"

**Inference rules**:
- Tool name contains: api, service, endpoint, webhook, http → **api**
- Operation pattern: POST/GET to external URL → **api**
- Default when uncertain and not database/storage/queue → **api**

---

### queue
**Definition**: Message queuing and pub/sub systems.

**Keywords**: queue, sqs, kafka, rabbitmq, pubsub, sns, nats, azure-queue, ibm-mq, activemq, kinesis, redis-queue

**Examples**:
- "publish to Kafka topic"
- "send to SQS queue"
- "consume from queue"
- "publish event to SNS"
- "subscribe to pub/sub"

**Inference rules**:
- Tool name contains: queue, kafka, sqs, rabbitmq, pubsub, sns → **queue**
- Action is "publish"/"subscribe"/"consume" → likely **queue**

---

### cache
**Definition**: In-memory caching and session stores.

**Keywords**: cache, redis, memcached, caching, session store, cache layer, memory store

**Examples**:
- "fetch from Redis"
- "set cache key"
- "invalidate memcached"
- "read from cache"
- "store in session cache"

**Inference rules**:
- Tool name contains: redis, memcached, cache → **cache**
- Operation is "set"/"get" on key-value → could be cache (but might be generic, infer from context)

---

### null
**When to use**: When the resource type cannot be determined from the text and context. Always prefer inferring a type if possible; use `null` only when ambiguous.

**Examples**:
- "process the data" (unclear what kind of data)
- "read the item" (no context about item type)
- "store the result" (could be database, cache, or storage)

---

## Sensitivity Labels

The sensitivity field indicates how sensitive the data being accessed is likely to be.

### public
**Definition**: Data that can be freely shared, accessed by unauthorized users, or publicly available.

**Keywords**: public, open, external, unrestricted, shared, published, anonymous, guest, community, forum, marketplace

**Examples**:
- "fetch public repositories"
- "read public comments"
- "get published articles"
- "list marketplace items"
- "retrieve open-source information"

**Inference rules**:
- Context explicitly says "public"
- Resource is in public/shared area
- Data type is known to be non-sensitive (product catalogs, blog posts, public profiles)

---

### internal
**Definition**: Data restricted to the organization; requires authentication but not highly sensitive.

**Keywords**: internal, private, confidential, restricted, company, organization, team, employee, staff, internal-only

**Examples**:
- "read user profiles" (company users)
- "fetch employee directory"
- "get company configuration"
- "query internal metrics"
- "retrieve team data"

**Inference rules**:
- Resource contains: users, employees, team, config, settings, metrics
- Action accesses personal/organizational data but not explicitly sensitive
- Default assumption when context implies company data

---

### secret
**Definition**: Highly sensitive data including PII, credentials, financial info, health data, or other regulated information.

**Keywords**: secret, sensitive, pii, phi, credential, password, token, key, api-key, private-key, authentication, payment, credit-card, ssn, personal-info, health, medical, financial, encrypted, secure

**Examples**:
- "read user passwords"
- "fetch API keys"
- "retrieve credit card information"
- "query patient medical records"
- "get SSN data"
- "access authentication tokens"

**Inference rules**:
- Resource name contains: password, token, key, credential, secret, private, auth
- Resource is known to contain: PII, PHI, financial, health, encryption keys
- Action accesses: user passwords, API keys, payment info, medical records
- Data type matches sensitive categories: email, phone, SSN, credit card, etc.

---

### null
**When to use**: When sensitivity cannot be determined from available context.

**Examples**:
- "query some data" (no indication of what data)
- "read records" (record type unknown)
- "access the resource" (resource type/content unknown)

---

## Labeling Decision Trees

Use these decision trees when you're uncertain about a label.

### Action Decision Tree

```
Does the operation modify/change data?
├─ YES
│  └─ Creating new records?
│     ├─ YES → write
│     └─ NO → Updating existing records?
│        ├─ YES → update
│        └─ NO → delete (or modify/remove)
└─ NO
   └─ Extracting data to external destination?
      ├─ YES → export
      └─ NO → Running a function/process?
         ├─ YES → execute
         └─ NO → read (retrieve/access)
```

### Resource Type Decision Tree

```
What's being accessed?
├─ Structured data (tables, collections, records)?
│  └─ database
├─ Unstructured files/blobs?
│  └─ storage
├─ HTTP/REST endpoint?
│  └─ api
├─ Message queue/pub-sub?
│  └─ queue
├─ Key-value cache?
│  └─ cache
└─ Unknown → null
```

### Sensitivity Decision Tree

```
Is sensitive data being accessed?
├─ Explicitly marked as public/shared/external?
│  └─ public
├─ Company/organization-only data?
│  └─ internal
├─ High-sensitivity data (PII, credentials, health, financial)?
│  └─ secret
└─ Unknown or cannot be determined → null
```

---

## Edge Cases & Disambiguation

### "upsert" (Create or Update)
**Ruling**: Label as **write** (not update)

**Reasoning**: Upsert's primary purpose is creation (if the record doesn't exist). Update only modifies existing records. Since upsert can create, treat it as write.

**Examples**:
- "upsert user record" → action: write
- "upsert or create item" → action: write

---

### "query" vs "read"
**Ruling**: Both map to **read**. Use "query" in context if tool is SQL query tool, use "read" as the canonical label.

**Examples**:
- "query the database" → action: read
- "query users" → action: read
- "database query" → action: read

---

### "list", "search", "find"
**Ruling**: All map to **read**

**Examples**:
- "list all files" → action: read
- "search the index" → action: read
- "find user by email" → action: read

---

### "copy" and "replicate"
**Ruling**: Depends on destination
- **copy** within the same system → **read**
- **copy** to external system → **export**
- **replicate** to backup → **export**
- **replicate** for failover/redundancy → **export**

**Examples**:
- "copy file to temp location" → action: read (within system)
- "copy data to S3" → action: export (external)
- "replicate database to backup" → action: export

---

### "download" vs "export"
**Ruling**: Both map to **export**. Download is typically from external, export is typically to external.

**Examples**:
- "download report" → action: export
- "export data to CSV" → action: export

---

### Generic "data" without type context
**Example**: "read data from source"

**Approach**:
1. Try to infer from tool_name or resource_location
2. If source is a known database tool → resource_type: database
3. If source is a known storage tool → resource_type: storage
4. Otherwise → resource_type: null

---

### Ambiguous "process" and "handle"
**Example**: "process the event" or "handle the request"

**Approach**:
1. If modifying data → lean towards update/write
2. If just reading and computing → read
3. If triggering something → execute
4. Default to execute if unclear

**Examples**:
- "process event from queue" → action: execute (or read, depending on context)
- "process form submission" → action: write (creating record)

---

### "insert" vs "append" vs "write"
**Ruling**: All map to **write**

**Distinction**:
- insert: add to database/structured
- append: add to file/list
- write: generic create/add

**Examples**:
- "insert into table" → action: write
- "append to log file" → action: write
- "write to cache" → action: write

---

### Sensitivity of "metrics", "logs", "analytics"
**Default**: **internal** (organization-level data)

**Exception**: If explicitly marked as user-facing analytics → **internal** (still org-level)

**Exception**: If logs contain PII or passwords → **secret**

**Examples**:
- "query metrics" → sensitivity: internal
- "read application logs" → sensitivity: internal
- "get user analytics" → sensitivity: internal

---

### Sensitivity when exact data type is unknown
**Heuristic**:
- If resource name suggests sensitive data (user, account, payment, health) → **secret**
- If resource name suggests standard data (product, item, article) → **public** or **internal**
- When truly unknown → **null**

**Examples**:
- "read records" (unknown type) → sensitivity: null
- "read users" (user data) → sensitivity: secret
- "read products" (product data) → sensitivity: public

---

## Inference Rules

### From Tool Name to Resource Type

| Tool Name Pattern | Resource Type |
|-------------------|---------------|
| Contains: db, sql, postgres, mongo, dynamo, cassandra, elastic | **database** |
| Contains: s3, gcs, blob, bucket, file, storage | **storage** |
| Contains: sqs, kafka, queue, sns, pubsub, kinesis | **queue** |
| Contains: redis, memcached, cache | **cache** |
| Contains: api, service, endpoint, webhook, http | **api** |
| Default | **api** (safest generic assumption) |

### From Action to Resource Type (Context-Dependent)

| Action | Tool Name Hint | Likely Resource |
|--------|----------------|-----------------|
| read | query, fetch, select | database |
| read | list, browse, search | storage or api |
| write | create, post | api |
| write | insert | database |
| delete | drop | database |
| execute | invoke, call, trigger | api |
| export | download, backup, dump | storage or external api |

### From Resource Name Keywords to Sensitivity

| Resource Name Contains | Sensitivity |
|------------------------|-------------|
| user, account, profile, person, customer, employee, email, phone, ssn | **secret** |
| password, token, key, credential, secret, private | **secret** |
| payment, credit-card, financial, medical, health, diagnosis | **secret** |
| config, settings, internal, metrics | **internal** |
| product, item, article, catalog, published, public | **public** |
| Unknown/ambiguous | **null** |

---

## Examples: Full Labeling Workflow

### Example 1: Clear Case
**Raw Text**: "fetch user email from the database"

**Analysis**:
- Action: "fetch" → read
- Resource: "database" → database
- Sensitivity: "user email" is PII → secret
- Confidence: high (all signals clear)

**Label**:
```json
{
  "action": "read",
  "resource_type": "database",
  "sensitivity": "secret"
}
```

---

### Example 2: Ambiguous Case
**Raw Text**: "upsert record in dynamodb"

**Analysis**:
- Action: "upsert" → write (per rules, treat as write not update)
- Resource: "dynamodb" keyword → database
- Sensitivity: "record" is generic → null (cannot determine if PII, config, product, etc.)
- Confidence: medium (sensitivity unclear)

**Label**:
```json
{
  "action": "write",
  "resource_type": "database",
  "sensitivity": null
}
```

---

### Example 3: Inferred Resource Type
**Raw Text**: "list all available services"
**Context**: { "tool_name": "stripe-api", "tool_method": "GET /services" }

**Analysis**:
- Action: "list" → read
- Resource: Not explicit, but "stripe-api" → api (from tool name inference)
- Sensitivity: "services" generic but Stripe context → likely payment-related → secret or internal
  - Reasoning: Stripe is payment platform, even "services" could be payment-related
  - Conservative: **secret** or **internal**? → internal (services are catalog, not transactions)
- Confidence: medium-high (resource inferred, sensitivity inferred)

**Label**:
```json
{
  "action": "read",
  "resource_type": "api",
  "sensitivity": "internal"
}
```

---

### Example 4: Execute/Trigger
**Raw Text**: "invoke payment webhook to notify external system"

**Analysis**:
- Action: "invoke" → execute (triggering an action, not data access)
- Resource: "webhook" → api (external)
- Sensitivity: "payment webhook" → secret (payment data context)
- Confidence: high

**Label**:
```json
{
  "action": "execute",
  "resource_type": "api",
  "sensitivity": "secret"
}
```

---

## Summary: Quick Reference

| Aspect | Canonical Values |
|--------|------------------|
| **Actions** | read, write, update, delete, execute, export |
| **Resource Types** | database, storage, api, queue, cache, null |
| **Sensitivities** | public, internal, secret, null |

When in doubt:
1. Check decision trees (above)
2. Reference examples in this document
3. Apply edge case rules if applicable
4. Default to **null** for fields you cannot determine confidently
5. Err on side of higher sensitivity (secret > internal > public)

---

For more examples and practical guidance, see the main SKILL.md file.
