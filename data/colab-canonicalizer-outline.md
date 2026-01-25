# Colab Notebook Outline - Canonicalizer TinyBERT Training

## Cell 01 - Markdown - Title
Canonicalizer TinyBERT Training

Goal: fine-tune a multi-head classifier to map free-form intent text to canonical labels for action, resource_type, and sensitivity.

Refs:
- docs/implementation/02-canonicalization-plan.md
- data/seed/seed.jsonl

## Cell 02 - Code - Install dependencies
```python
!pip -q install "transformers>=4.36" datasets accelerate evaluate scikit-learn onnx onnxruntime onnxruntime-tools
```

## Cell 03 - Code - Mount Google Drive
```python
from google.colab import drive
drive.mount("/content/drive")
```

## Cell 04 - Markdown - Config
- Backbone: huawei-noah/TinyBERT_General_4L_312D
- Max length: 128
- Batch size: 32
- Epochs: 3-5
- Optimizer: AdamW, lr=2e-5, warmup 10%

## Cell 05 - Code - Imports
```python
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from datasets import Dataset
from sklearn.model_selection import train_test_split
from transformers import (AutoTokenizer, AutoModel, DataCollatorWithPadding,
                          get_linear_schedule_with_warmup)
```

## Cell 06 - Code - Paths and hyperparameters
```python
DATA_PATH = Path("/content/drive/MyDrive/guard/seed.jsonl")
OUTPUT_DIR = Path("/content/drive/MyDrive/guard/models/canonicalizer_tinybert_v1.0")
MODEL_ID = "huawei-noah/TinyBERT_General_4L_312D"

MAX_LENGTH = 128
BATCH_SIZE = 32
EPOCHS = 5
LR = 2e-5
WARMUP_RATIO = 0.1
SEED = 42
```

## Cell 07 - Code - Load JSONL
```python
df = pd.read_json(DATA_PATH, lines=True)
df.head(3)
```

## Cell 08 - Code - Build model input text
```python
def build_text(row):
    raw_text = row.get("raw_text", "")
    context = row.get("context") or {}
    tool_name = context.get("tool_name")
    tool_method = context.get("tool_method")
    resource_location = context.get("resource_location")

    parts = [raw_text]
    if tool_name:
        parts.append(f"tool_name: {tool_name}")
    if tool_method:
        parts.append(f"tool_method: {tool_method}")
    if resource_location:
        parts.append(f"resource_location: {resource_location}")

    return " [CTX] ".join(parts)

df["text"] = df.apply(build_text, axis=1)
```

## Cell 09 - Code - Label vocab (canonical)
```python
ACTION_LABELS = ["read", "write", "update", "delete", "execute", "export"]
RESOURCE_LABELS = ["database", "storage", "api", "queue", "cache"]
SENSITIVITY_LABELS = ["public", "internal", "secret"]

action2id = {label: idx for idx, label in enumerate(ACTION_LABELS)}
resource2id = {label: idx for idx, label in enumerate(RESOURCE_LABELS)}
sensitivity2id = {label: idx for idx, label in enumerate(SENSITIVITY_LABELS)}
```

## Cell 10 - Code - Encode labels with null handling
```python
def encode_label(value, mapping, missing_value=-100):
    if value is None:
        return missing_value
    return mapping.get(value, missing_value)

labels = df["labels"].apply(lambda item: item or {})
df["action_id"] = labels.apply(lambda item: encode_label(item.get("action"), action2id))
df["resource_id"] = labels.apply(lambda item: encode_label(item.get("resource_type"), resource2id))
df["sensitivity_id"] = labels.apply(lambda item: encode_label(item.get("sensitivity"), sensitivity2id))
```

## Cell 11 - Code - Train/val/test split (stratified)
```python
df["stratify_key"] = (
    df["action_id"].astype(str)
    + "|" + df["resource_id"].astype(str)
    + "|" + df["sensitivity_id"].astype(str)
)

def safe_split(frame, test_size, seed):
    counts = frame["stratify_key"].value_counts()
    rare_keys = counts[counts < 2].index
    frame = frame.copy()
    frame["stratify_key_adj"] = frame["stratify_key"].where(
        ~frame["stratify_key"].isin(rare_keys), other="rare"
    )
    if frame["stratify_key_adj"].value_counts().min() < 2:
        return train_test_split(frame, test_size=test_size, random_state=seed, shuffle=True)
    return train_test_split(
        frame,
        test_size=test_size,
        random_state=seed,
        stratify=frame["stratify_key_adj"],
    )

train_df, temp_df = safe_split(df, test_size=0.2, seed=SEED)
val_df, test_df = safe_split(temp_df, test_size=0.5, seed=SEED)
```

## Cell 12 - Code - Tokenizer and dataset conversion
```python
tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)

def tokenize(batch):
    return tokenizer(batch["text"], truncation=True, max_length=MAX_LENGTH)

train_ds = Dataset.from_pandas(train_df).map(tokenize, batched=True)
val_ds = Dataset.from_pandas(val_df).map(tokenize, batched=True)
test_ds = Dataset.from_pandas(test_df).map(tokenize, batched=True)

columns = ["input_ids", "attention_mask", "action_id", "resource_id", "sensitivity_id"]
train_ds.set_format(type="torch", columns=columns)
val_ds.set_format(type="torch", columns=columns)
test_ds.set_format(type="torch", columns=columns)
```

## Cell 13 - Code - Data collator
```python
data_collator = DataCollatorWithPadding(tokenizer=tokenizer)
```

## Cell 14 - Code - Multi-head TinyBERT model
```python
import torch.nn as nn

class TinyBertMultiHead(nn.Module):
    def __init__(self, base_model_name, num_actions, num_resources, num_sensitivities):
        super().__init__()
        self.encoder = AutoModel.from_pretrained(base_model_name)
        hidden_size = self.encoder.config.hidden_size
        self.action_head = nn.Linear(hidden_size, num_actions)
        self.resource_head = nn.Linear(hidden_size, num_resources)
        self.sensitivity_head = nn.Linear(hidden_size, num_sensitivities)

    def forward(self, input_ids, attention_mask=None):
        outputs = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
        pooled = outputs.last_hidden_state[:, 0]
        return {
            "action_logits": self.action_head(pooled),
            "resource_logits": self.resource_head(pooled),
            "sensitivity_logits": self.sensitivity_head(pooled),
        }

model = TinyBertMultiHead(
    MODEL_ID,
    num_actions=len(ACTION_LABELS),
    num_resources=len(RESOURCE_LABELS),
    num_sensitivities=len(SENSITIVITY_LABELS),
)
```

## Cell 15 - Code - Loss function helper
```python
def compute_loss(logits, labels):
    loss_fn = nn.CrossEntropyLoss(ignore_index=-100)
    loss_action = loss_fn(logits["action_logits"], labels["action_id"])
    loss_resource = loss_fn(logits["resource_logits"], labels["resource_id"])
    loss_sensitivity = loss_fn(logits["sensitivity_logits"], labels["sensitivity_id"])
    return loss_action + loss_resource + loss_sensitivity
```

## Cell 16 - Code - DataLoaders
```python
from torch.utils.data import DataLoader

train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True, collate_fn=data_collator)
val_loader = DataLoader(val_ds, batch_size=BATCH_SIZE, shuffle=False, collate_fn=data_collator)
test_loader = DataLoader(test_ds, batch_size=BATCH_SIZE, shuffle=False, collate_fn=data_collator)
```

## Cell 17 - Code - Training loop (full)
```python
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model.to(device)

def accuracy_from_logits(logits, labels):
    preds = torch.argmax(logits, dim=-1)
    mask = labels != -100
    if mask.sum() == 0:
        return 0.0
    return (preds[mask] == labels[mask]).float().mean().item()

optimizer = torch.optim.AdamW(model.parameters(), lr=LR)
total_steps = len(train_loader) * EPOCHS
warmup_steps = int(total_steps * WARMUP_RATIO)
scheduler = get_linear_schedule_with_warmup(optimizer, warmup_steps, total_steps)

best_val_loss = float("inf")
patience = 2
patience_counter = 0
best_state = None

for epoch in range(EPOCHS):
    model.train()
    train_losses = []

    for batch in train_loader:
        batch = {k: v.to(device) for k, v in batch.items()}
        logits = model(batch["input_ids"], attention_mask=batch["attention_mask"])
        loss = compute_loss(logits, batch)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        scheduler.step()
        optimizer.zero_grad()
        train_losses.append(loss.item())

    model.eval()
    val_losses = []
    val_action_acc = []
    val_resource_acc = []
    val_sensitivity_acc = []

    with torch.no_grad():
        for batch in val_loader:
            batch = {k: v.to(device) for k, v in batch.items()}
            logits = model(batch["input_ids"], attention_mask=batch["attention_mask"])
            loss = compute_loss(logits, batch)
            val_losses.append(loss.item())
            val_action_acc.append(accuracy_from_logits(logits["action_logits"], batch["action_id"]))
            val_resource_acc.append(accuracy_from_logits(logits["resource_logits"], batch["resource_id"]))
            val_sensitivity_acc.append(accuracy_from_logits(logits["sensitivity_logits"], batch["sensitivity_id"]))

    train_loss = float(np.mean(train_losses))
    val_loss = float(np.mean(val_losses))
    print(
        f"epoch {epoch + 1}/{EPOCHS} "
        f"train_loss={train_loss:.4f} val_loss={val_loss:.4f} "
        f"action={np.mean(val_action_acc):.4f} "
        f"resource={np.mean(val_resource_acc):.4f} "
        f"sensitivity={np.mean(val_sensitivity_acc):.4f}"
    )

    if val_loss < best_val_loss:
        best_val_loss = val_loss
        patience_counter = 0
        best_state = {k: v.detach().cpu() for k, v in model.state_dict().items()}
    else:
        patience_counter += 1
        if patience_counter >= patience:
            print("early stopping")
            break

if best_state is not None:
    model.load_state_dict(best_state)
```

## Cell 18 - Code - Test set evaluation
```python
# TODO: mirror validation pass for test_loader
```

## Cell 19 - Code - Save checkpoint and label maps
```python
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

model.encoder.save_pretrained(OUTPUT_DIR / "encoder")
tokenizer.save_pretrained(OUTPUT_DIR / "tokenizer")

with open(OUTPUT_DIR / "label_maps.json", "w") as f:
    json.dump(
        {
            "action": action2id,
            "resource_type": resource2id,
            "sensitivity": sensitivity2id,
        },
        f,
        indent=2,
    )
```

## Cell 20 - Code - Quick inference sanity check
```python
sample_texts = [
    "query the users table [CTX] tool_name: database_query [CTX] tool_method: query",
    "export billing data [CTX] tool_name: stripe-api [CTX] tool_method: GET /v1/treasury/transactions",
]

encoded = tokenizer(sample_texts, truncation=True, max_length=MAX_LENGTH, padding=True, return_tensors="pt")
encoded = {k: v.to(device) for k, v in encoded.items()}

model.eval()
with torch.no_grad():
    logits = model(encoded["input_ids"], attention_mask=encoded["attention_mask"])

action_preds = torch.argmax(logits["action_logits"], dim=-1).cpu().tolist()
resource_preds = torch.argmax(logits["resource_logits"], dim=-1).cpu().tolist()
print("action preds", [ACTION_LABELS[idx] for idx in action_preds])
print("resource preds", [RESOURCE_LABELS[idx] for idx in resource_preds])
```

## Cell 21 - Code - Export to ONNX (CPU optimized, FP16)
```python
import torch.onnx
from onnxruntime.transformers import optimizer
from onnxruntime.transformers.fusion_options import FusionOptions

# Move model to CPU for ONNX export
model.to("cpu")
model.eval()

# Dummy input for tracing
dummy_input_ids = torch.zeros(1, MAX_LENGTH, dtype=torch.long)
dummy_attention_mask = torch.ones(1, MAX_LENGTH, dtype=torch.long)

# Export path
onnx_path = OUTPUT_DIR / "model.onnx"
opt_path = OUTPUT_DIR / "model_optimized.onnx"

# Export to ONNX
torch.onnx.export(
    model,
    (dummy_input_ids, dummy_attention_mask),
    onnx_path,
    input_names=["input_ids", "attention_mask"],
    output_names=["action_logits", "resource_logits", "sensitivity_logits"],
    dynamic_axes={
        "input_ids": {0: "batch_size", 1: "seq_len"},
        "attention_mask": {0: "batch_size", 1: "seq_len"},
        "action_logits": {0: "batch_size"},
        "resource_logits": {0: "batch_size"},
        "sensitivity_logits": {0: "batch_size"},
    },
    opset_version=14,
    do_constant_folding=True,
)

print(f"Exported ONNX model to {onnx_path}")

# CPU optimization with ONNX Runtime
fusion_options = FusionOptions("bert")
fusion_options.enable_skip_layer_norm = True
fusion_options.enable_embed_layer_norm = True

optimized_model = optimizer.optimize_model(
    str(onnx_path),
    model_type="bert",
    num_heads=12,  # TinyBERT 4L has 12 attention heads
    hidden_size=312,
    optimization_options=fusion_options,
)

# Convert to FP16 for faster CPU inference
optimized_model.convert_float_to_float16(keep_io_types=True)
optimized_model.save_model_to_file(str(opt_path))

print(f"CPU-optimized FP16 ONNX model saved to {opt_path}")
```
