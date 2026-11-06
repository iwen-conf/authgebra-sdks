mod common;

use authgebra_client::generated::models;
use serde::{Serialize, de::DeserializeOwned};
use serde_json::{Value, json};

fn spec() -> Value {
    serde_json::from_slice(&std::fs::read(common::contracts_dir().join("openapi.json")).unwrap())
        .unwrap()
}

// This oracle reads the contract at test time and does not use generator metadata or examples.
fn sample(document: &Value, schema: &Value) -> Value {
    if let Some(reference) = schema["$ref"].as_str() {
        return sample(
            document,
            document
                .pointer(reference.strip_prefix('#').unwrap())
                .unwrap(),
        );
    }
    if let Some(values) = schema["enum"].as_array() {
        return values[0].clone();
    }
    for key in ["oneOf", "anyOf", "allOf"] {
        if let Some(arms) = schema[key].as_array() {
            return sample(
                document,
                arms.iter().find(|arm| arm["type"] != "null").unwrap(),
            );
        }
    }
    let kind = schema["type"].as_str().or_else(|| {
        schema["type"].as_array().and_then(|types| {
            types
                .iter()
                .filter_map(Value::as_str)
                .find(|kind| *kind != "null")
        })
    });
    match kind {
        Some("object") => {
            let mut object = serde_json::Map::new();
            if let Some(required) = schema["required"].as_array() {
                for field in required.iter().filter_map(Value::as_str) {
                    object.insert(
                        field.to_owned(),
                        sample(document, &schema["properties"][field]),
                    );
                }
            }
            Value::Object(object)
        }
        Some("array") => Value::Array(
            (0..schema["minItems"].as_u64().unwrap_or(1))
                .map(|_| sample(document, &schema["items"]))
                .collect(),
        ),
        Some("string") if schema["format"] == "uuid" => {
            json!("11111111-1111-4111-8111-111111111111")
        }
        Some("string") => json!("x".repeat(schema["minLength"].as_u64().unwrap_or(1) as usize)),
        Some("integer") => schema.get("minimum").cloned().unwrap_or(json!(1)),
        Some("number") => json!(1.5),
        Some("boolean") => json!(false),
        Some("null") => Value::Null,
        None => json!({"arbitrary": [false, null, 9007199254740993_i64]}),
        other => panic!("unsupported oracle schema: {other:?}"),
    }
}

fn roundtrip<T: Serialize + DeserializeOwned>(document: &Value, pointer: &str) {
    let expected = sample(document, document.pointer(pointer).unwrap());
    let decoded: T = serde_json::from_value(expected.clone())
        .unwrap_or_else(|error| panic!("{pointer}: {error}"));
    assert_eq!(
        serde_json::to_value(decoded).unwrap(),
        expected,
        "{pointer}"
    );
}

include!("../src/generated/model_tests.rs");

fn exception_sample(document: &Value, pointer: &str, target: &str, value: &Value) -> Value {
    if pointer == target {
        return value.clone();
    }
    let schema = document
        .pointer(pointer.strip_prefix('#').unwrap())
        .unwrap();
    if let Some(reference) = schema["$ref"].as_str() {
        return exception_sample(document, reference, target, value);
    }
    if let Some(props) = schema["properties"].as_object() {
        let mut result = serde_json::Map::new();
        for (name, prop) in props {
            let child = format!("{pointer}/properties/{name}");
            if target.starts_with(&child) {
                result.insert(
                    name.clone(),
                    exception_sample(document, &child, target, value),
                );
            } else if schema["required"]
                .as_array()
                .is_some_and(|required| required.contains(&json!(name)))
            {
                result.insert(name.clone(), sample(document, prop));
            }
        }
        return Value::Object(result);
    }
    if schema.get("items").is_some() {
        return json!([exception_sample(
            document,
            &format!("{pointer}/items"),
            target,
            value
        )]);
    }
    if schema.get("additionalProperties").is_some() {
        return json!({"example":exception_sample(document, &format!("{pointer}/additionalProperties"), target, value)});
    }
    sample(document, schema)
}

fn exception_roundtrip<T: Serialize + DeserializeOwned>(document: &Value, pointer: &str) {
    let component = pointer.split('/').nth(3).unwrap();
    let root = format!("#/components/schemas/{component}");
    for value in [
        Value::Null,
        json!(false),
        json!(0),
        json!(""),
        json!([]),
        json!({"nested":[null,u64::MAX]}),
    ] {
        let input = exception_sample(document, &root, pointer, &value);
        let decoded: T = serde_json::from_value(input.clone())
            .unwrap_or_else(|error| panic!("{pointer}: {error}"));
        assert_eq!(serde_json::to_value(decoded).unwrap(), input, "{pointer}");
    }
}

#[test]
fn required_nullable_and_patch_presence_are_distinct() {
    assert!(
        serde_json::from_value::<models::SetUserMfaPolicyRequest>(
            json!({"reauth":{"password":"secret"}})
        )
        .is_err()
    );
    let required: models::SetUserMfaPolicyRequest =
        serde_json::from_value(json!({"mode":null,"reauth":{"password":"secret"}})).unwrap();
    assert!(serde_json::to_value(required).unwrap()["mode"].is_null());
    for input in [
        json!({}),
        json!({"description":null}),
        json!({"description":""}),
    ] {
        let value: models::PatchRoleRequest = serde_json::from_value(input.clone()).unwrap();
        assert_eq!(serde_json::to_value(value).unwrap(), input);
    }
}

#[test]
fn optional_non_nullable_collections_reject_explicit_null() {
    let value = json!({"username":"reader","display_name":"Reader","initial_password":"long-password", "role_ids":null});
    assert!(serde_json::from_value::<models::CreateUserRequest>(value).is_err());
    assert!(
        serde_json::from_value::<models::CreateRelationDefinitionRequest>(json!({
            "object_type":"doc","relation":"viewer","subject_types":["user"],"rewrites":null
        }))
        .is_err()
    );
}

#[test]
fn open_json_preserves_all_kinds() {
    let vectors: Value = serde_json::from_slice(
        &std::fs::read(common::fixtures_dir().join("request-semantics.json")).unwrap(),
    )
    .unwrap();
    for value in vectors["patch_values"]
        .as_array()
        .unwrap()
        .iter()
        .chain(vectors["integer_values"].as_array().unwrap())
    {
        let input = json!({"attribute_id":"11111111-1111-4111-8111-111111111111", "value":value});
        let typed: models::AttributeAssignmentDto = serde_json::from_value(input.clone()).unwrap();
        assert_eq!(serde_json::to_value(typed).unwrap(), input);
    }
    let unsigned = json!({"type":"u64","value":u64::MAX});
    let typed: models::TypedValueDto = serde_json::from_value(unsigned.clone()).unwrap();
    assert_eq!(serde_json::to_value(typed).unwrap(), unsigned);
}

#[test]
fn explicit_validation_uses_the_existing_bounds() {
    for (limit, valid) in [(0, false), (1, true), (100, true), (101, false)] {
        let request: models::ListResourcesRequest = serde_json::from_value(json!({
            "subject_id":"11111111-1111-4111-8111-111111111111","permission":"read","resource_type":"doc","limit":limit
        })).unwrap();
        assert_eq!(request.validate().is_ok(), valid);
    }
    for (count, valid) in [(0, false), (1, true), (10000, true), (10001, false)] {
        let request: models::BatchCheckRequest = serde_json::from_value(json!({
            "subject_id":"11111111-1111-4111-8111-111111111111","permission":"read",
            "resources":vec![json!({"resource_type_code":"doc","external_id":"id"});count]
        }))
        .unwrap();
        assert_eq!(request.validate().is_ok(), valid);
    }
}

#[test]
fn iam_machine_constraints_match_server_bounds() {
    for code in ["", "Uppercase", "valid\n", "x ", "é"] {
        let value: models::CreateRoleRequest =
            serde_json::from_value(json!({"code":code,"name":"valid"})).unwrap();
        assert!(value.validate().is_err());
    }
    for length in [1, 64, 65] {
        let value: models::CreateRoleRequest =
            serde_json::from_value(json!({"code":"valid","name":"界".repeat(length)})).unwrap();
        assert_eq!(value.validate().is_ok(), length <= 64);
    }
    for size in [0, 512, 513] {
        let roles: Vec<String> = (0..size)
            .map(|value| format!("00000000-0000-4000-8000-{value:012}"))
            .collect();
        let value: models::ReplaceUserRolesRequest =
            serde_json::from_value(json!({"role_ids":roles})).unwrap();
        assert_eq!(value.validate().is_ok(), size <= 512);
    }
    let value: models::ReplaceUserRolesRequest = serde_json::from_value(json!({"role_ids":["00000000-0000-4000-8000-000000000001","00000000-0000-4000-8000-000000000001"]})).unwrap();
    assert!(value.validate().is_err());
}

#[test]
fn closed_request_import_rejects_unknown_fields_without_closing_responses() {
    assert!(serde_json::from_value::<models::CreateUserRequest>(json!({"username":"reader","display_name":"Reader","initial_password":"long-password", "password_hash":"unexpected"})).is_err());
    assert!(serde_json::from_value::<models::ResetPasswordRequest>(json!({"new_password":"long-password", "reauth":{"password":"secret", "unexpected":true}})).is_err());
    let mut response = sample(&spec(), &spec()["components"]["schemas"]["RoleDto"]);
    response["future_server_field"] = json!(true);
    assert!(serde_json::from_value::<models::RoleDto>(response).is_ok());
}
