use bridge::rule_vector::RuleVector;

#[test]
fn test_bincode_serialization_preserves_counts() {
    // Create a RuleVector with counts set
    let mut original = RuleVector::default();
    original.action_count = 5;
    original.resource_count = 3;
    original.data_count = 7;
    original.risk_count = 2;
    
    println!("Original counts: [{}, {}, {}, {}]", 
        original.action_count,
        original.resource_count, 
        original.data_count,
        original.risk_count
    );
    
    // Serialize
    let serialized = bincode::serialize(&original).unwrap();
    println!("Serialized size: {} bytes", serialized.len());
    
    // Deserialize
    let deserialized: RuleVector = bincode::deserialize(&serialized).unwrap();
    println!("Deserialized counts: [{}, {}, {}, {}]",
        deserialized.action_count,
        deserialized.resource_count,
        deserialized.data_count,
        deserialized.risk_count
    );
    
    // Check if they match
    assert_eq!(original.action_count, deserialized.action_count);
    assert_eq!(original.resource_count, deserialized.resource_count);
    assert_eq!(original.data_count, deserialized.data_count);
    assert_eq!(original.risk_count, deserialized.risk_count);
}
