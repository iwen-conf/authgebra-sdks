package authgebra

import (
	"bytes"
	"encoding/json"
	"fmt"
)

// Field preserves absence, explicit null, and a typed value. Its zero value is
// absent. Generated containing models omit absent fields in MarshalJSON.
type Field[T any] struct {
	state   uint8
	value   T
	decoded bool
}

// SetField includes value, even if it is false, zero, an empty string, or an empty array.
func SetField[T any](value T) Field[T] { return Field[T]{state: 2, value: value} }

// NullField includes an explicit null. Generated models reject it for non-nullable fields.
func NullField[T any]() Field[T] { return Field[T]{state: 1} }

// IsSet distinguishes an absent field from null or a value.
func (f Field[T]) IsSet() bool { return f.state != 0 }

// IsNull reports whether the field was explicitly set to null.
func (f Field[T]) IsNull() bool { return f.state == 1 }

// Value returns the typed value and whether this is the value state.
func (f Field[T]) Value() (T, bool) { return f.value, f.state == 2 }

func encodeField[T any](fields map[string]json.RawMessage, name string, field Field[T], nullable, clear bool) error {
	if field.state == 0 && !clear {
		return nil
	}
	if field.state != 0 && clear {
		return fmt.Errorf("%s has both Field and Clear values", name)
	}
	if field.decoded {
		if _, exists := fields[name]; exists {
			return nil
		}
	} else if _, exists := fields[name]; exists {
		return fmt.Errorf("%s has both a legacy value and a Field or Clear value", name)
	}
	if clear || field.state == 1 {
		if !nullable {
			return fmt.Errorf("%s does not accept null", name)
		}
		fields[name] = json.RawMessage("null")
		return nil
	}
	raw, err := marshalBody(field.value)
	if err != nil {
		return err
	}
	if !nullable && isJSONNull(raw) {
		return fmt.Errorf("%s does not accept null", name)
	}
	fields[name] = raw
	return nil
}
func decodedField[T any](raw json.RawMessage, value T) Field[T] {
	if raw == nil {
		return Field[T]{}
	}
	if bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
		return Field[T]{state: 1, decoded: true}
	}
	// The containing model already decoded and owns the typed legacy member.
	return Field[T]{state: 2, value: value, decoded: true}
}

func fieldPointerValue[T any](value *T) T {
	if value != nil {
		return *value
	}
	var zero T
	return zero
}

func validateUnique[T any](values []T) error {
	seen := make(map[string]bool, len(values))
	for _, value := range values {
		raw, err := json.Marshal(value)
		if err != nil {
			return err
		}
		key := string(raw)
		if seen[key] {
			return fmt.Errorf("duplicate array item")
		}
		seen[key] = true
	}
	return nil
}

func refreshField[T any](state Field[T], current T, present bool) Field[T] {
	if state.decoded && state.state == 2 {
		if !present {
			return Field[T]{}
		}
		state.value = current
	}
	return state
}

func isJSONNull(raw []byte) bool { return bytes.Equal(bytes.TrimSpace(raw), []byte("null")) }
