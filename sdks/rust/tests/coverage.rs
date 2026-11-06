//! The generated admin client must cover every operation in the reviewed OpenAPI export and
//! must not be stale relative to it.

mod common;

use std::collections::BTreeSet;

use authgebra_client::ErrorCode;
use authgebra_client::generated::admin::{OPENAPI_SHA256, OPERATIONS};

fn spec() -> serde_json::Value {
    let path = common::contracts_dir().join("openapi.json");
    serde_json::from_slice(
        &std::fs::read(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display())),
    )
    .unwrap()
}

#[test]
fn every_operation_has_a_method() {
    let source = include_str!("../src/generated/admin.rs");
    let mut ids = BTreeSet::new();
    let mut missing = Vec::new();
    for (path, item) in spec()["paths"].as_object().unwrap() {
        for (method, op) in item.as_object().unwrap() {
            let id = op["operationId"].as_str().unwrap().to_owned();
            if !source.contains(&format!("pub async fn {id}(")) {
                missing.push(format!("{id} ({method} {path})"));
            }
            assert!(
                OPERATIONS
                    .iter()
                    .any(|(oid, m, p)| *oid == id && m.eq_ignore_ascii_case(method) && p == path),
                "{id} missing from OPERATIONS"
            );
            ids.insert(id);
        }
    }
    assert!(
        missing.is_empty(),
        "operations without a method: {missing:?}"
    );
    assert_eq!(
        ids.len(),
        OPERATIONS.len(),
        "OPERATIONS has entries the spec no longer has (stale generation)"
    );
    println!("operations covered: {}/{}", ids.len(), ids.len());
}

#[test]
fn generation_matches_the_reviewed_openapi_digest() {
    let digest = std::fs::read_to_string(common::contracts_dir().join("openapi.sha256")).unwrap();
    assert_eq!(
        digest.trim(),
        OPENAPI_SHA256,
        "regenerate: python3 tools/gen.py ../contracts/openapi.json src/generated"
    );
}

#[test]
fn known_error_codes_equal_the_contract_enum() {
    let spec = spec();
    let declared: BTreeSet<String> = spec["components"]["schemas"]["ApiErrorCode"]["enum"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_owned())
        .collect();
    let known: BTreeSet<String> = ErrorCode::ALL
        .iter()
        .map(|c| c.as_str().to_owned())
        .collect();
    assert_eq!(known, declared);
}
