use serde::{Deserialize, Serialize};
use std::convert::TryInto;

pub const MAX_ANCHORS_PER_SLOT: usize = 16;
pub const SLOT_WIDTH: usize = 32;

/// Anchors for a single rule, stored per slot for sandbox comparisons.
#[derive(Clone, Debug, Serialize, Deserialize, Default)]
pub struct RuleVector {
    pub action_anchors: [[f32; SLOT_WIDTH]; MAX_ANCHORS_PER_SLOT],
    pub action_count: usize,
    pub resource_anchors: [[f32; SLOT_WIDTH]; MAX_ANCHORS_PER_SLOT],
    pub resource_count: usize,
    pub data_anchors: [[f32; SLOT_WIDTH]; MAX_ANCHORS_PER_SLOT],
    pub data_count: usize,
    pub risk_anchors: [[f32; SLOT_WIDTH]; MAX_ANCHORS_PER_SLOT],
    pub risk_count: usize,
}

/// Convert raw anchor data from the Management Plane into fixed-size buffers.
pub fn convert_anchor_block(
    slot: &str,
    anchors: &[Vec<f32>],
    count: usize,
) -> Result<([[f32; SLOT_WIDTH]; MAX_ANCHORS_PER_SLOT], usize), String> {
    if anchors.len() != MAX_ANCHORS_PER_SLOT {
        return Err(format!(
            "Slot '{}' returned {} anchors, expected {}",
            slot,
            anchors.len(),
            MAX_ANCHORS_PER_SLOT
        ));
    }

    if count > MAX_ANCHORS_PER_SLOT {
        return Err(format!(
            "Slot '{}' count {} exceeds max {}",
            slot, count, MAX_ANCHORS_PER_SLOT
        ));
    }

    let mut block = [[0f32; SLOT_WIDTH]; MAX_ANCHORS_PER_SLOT];

    for (idx, row) in anchors.iter().enumerate() {
        if row.len() != SLOT_WIDTH {
            return Err(format!(
                "Slot '{}' anchor {} has length {}, expected {}",
                slot,
                idx,
                row.len(),
                SLOT_WIDTH
            ));
        }

        block[idx] = row
            .as_slice()
            .try_into()
            .map_err(|_| format!("Failed to convert {} anchor {} into array", slot, idx))?;
    }

    Ok((block, count))
}
