use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Header {
    magic: u32,
    version: u32,
    entry_count: u32,
    index_offset: u64,
}

fn main() {
    let header = Header {
        magic: 0x47554152,
        version: 1,
        entry_count: 0,
        index_offset: 0,
    };
    let serialized = bincode::serialize(&header).unwrap();
    println!("Header size: {}", serialized.len());
}
