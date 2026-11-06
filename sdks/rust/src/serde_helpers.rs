//! Three-state optional fields: absent / `null` / value.

use serde::{Deserialize, Deserializer};

/// A required nullable field accepts null, but Serde must still reject a missing key.
pub fn required_option<'de, T, D>(de: D) -> Result<Option<T>, D::Error>
where
    T: Deserialize<'de>,
    D: Deserializer<'de>,
{
    Option::<T>::deserialize(de)
}

/// Missing optional fields use their default; present fields must decode as a non-null T.
pub fn optional_non_nullable<'de, T, D>(de: D) -> Result<Option<T>, D::Error>
where
    T: Deserialize<'de>,
    D: Deserializer<'de>,
{
    T::deserialize(de).map(Some)
}

/// Deserialize `Option<Option<T>>` so that `null` is `Some(None)` and a missing key (with
/// `#[serde(default)]`) is `None`.
pub fn double_option<'de, T, D>(de: D) -> Result<Option<Option<T>>, D::Error>
where
    T: Deserialize<'de>,
    D: Deserializer<'de>,
{
    Option::<T>::deserialize(de).map(Some)
}

/// The ASCII code grammar shared by the server's IAM, resource type and policy validators.
pub fn code_is_valid(value: &str) -> bool {
    let bytes = value.as_bytes();
    (1..=64).contains(&bytes.len())
        && bytes[0].is_ascii_lowercase()
        && bytes.iter().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || matches!(byte, b'_' | b'.' | b'-')
        })
}
