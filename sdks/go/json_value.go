package authgebra

import (
	"bytes"
	"encoding/json"
	"errors"
)

// JSONValue is an explicitly open JSON value from the source schema. It keeps
// integer and decimal tokens intact rather than decoding numbers through float64.
// Its zero value is JSON null; known objects use generated models instead.
type JSONValue struct{ raw []byte }

// ParseJSONValue validates and copies a JSON value, including arrays and scalars.
func ParseJSONValue(raw []byte) (JSONValue, error) {
	if !json.Valid(raw) {
		return JSONValue{}, errors.New("authgebra: invalid open JSON value")
	}
	return JSONValue{raw: bytes.Clone(raw)}, nil
}

// MarshalJSON preserves the original value's JSON numeric precision.
func (v JSONValue) MarshalJSON() ([]byte, error) {
	if len(v.raw) == 0 {
		return []byte("null"), nil
	}
	return bytes.Clone(v.raw), nil
}

// UnmarshalJSON validates the value and takes ownership of its own copy.
func (v *JSONValue) UnmarshalJSON(raw []byte) error {
	parsed, err := ParseJSONValue(raw)
	if err != nil {
		return err
	}
	*v = parsed
	return nil
}

// JSONObject represents an object whose property names and values are open in OpenAPI.
type JSONObject map[string]JSONValue
