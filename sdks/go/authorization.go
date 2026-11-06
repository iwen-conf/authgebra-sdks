package authgebra

import "context"

// AuthorizationClient calls the decision endpoints. It never evaluates policy
// itself. A "deny" is a normal successful Result; only protocol/system problems
// (for example 503 NOT_READY) are errors — and those must NOT be treated as a
// deny that was actually computed.
type AuthorizationClient struct{ c *Client }

// Allowed reports whether the decision is "allow".
func (r CheckResponse) Allowed() bool { return r.Decision == DecisionAllow }

// Check evaluates one subject/permission/resource.
func (a *AuthorizationClient) Check(ctx context.Context, req CheckRequest, opts ...CallOption) (*CheckResult, error) {
	return a.c.Authz.Check(ctx, req, opts...)
}

// BatchCheck evaluates one subject/permission against many resources (order preserved).
func (a *AuthorizationClient) BatchCheck(ctx context.Context, req BatchCheckRequest, opts ...CallOption) (*BatchCheckResult, error) {
	return a.c.Authz.BatchCheck(ctx, req, opts...)
}

// Explain evaluates like Check and returns the decision trace.
func (a *AuthorizationClient) Explain(ctx context.Context, req ExplainRequest, opts ...CallOption) (*ExplainResult, error) {
	return a.c.Authz.Explain(ctx, req, opts...)
}

// Revision reads the published authorization revision status.
func (a *AuthorizationClient) Revision(ctx context.Context, opts ...CallOption) (*RevisionResult, error) {
	return a.c.Authz.Revision(ctx, opts...)
}

// ListResources returns one page of authorized resources. Pass the previous
// page's Page.NextCursor in req.Cursor; the cursor is opaque and bound to the
// query and snapshot generation, so a stale cursor yields *APIError CURSOR_STALE.
func (a *AuthorizationClient) ListResources(ctx context.Context, req ListResourcesRequest, opts ...CallOption) (*ListAuthorizedResourcesResult, error) {
	return a.c.Authz.ListAuthorizedResources(ctx, req, opts...)
}

// PageOptions bounds an iterator.
type PageOptions struct {
	// MaxPages caps the number of pages fetched (default 100). Reaching it ends
	// iteration with ErrMaxPages.
	MaxPages int
}

// ResourceIterator walks authorized-resource pages one at a time (pull-based, so
// the caller controls pace and cancellation through ctx). It never restarts on
// CURSOR_STALE and never merges pages of different snapshot generations: those
// end iteration with the typed error from Err.
type ResourceIterator struct {
	a        *AuthorizationClient
	req      ListResourcesRequest
	opts     []CallOption
	max      int
	fetched  int
	done     bool
	err      error
	page     *ListAuthorizedResourcesResult
	firstGen *int64
}

// ListResourcesPages returns an iterator starting at req.Cursor (nil = first page).
func (a *AuthorizationClient) ListResourcesPages(req ListResourcesRequest, po PageOptions, opts ...CallOption) *ResourceIterator {
	m := po.MaxPages
	if m <= 0 {
		m = 100
	}
	return &ResourceIterator{a: a, req: req, opts: opts, max: m}
}

// Next fetches the next page; it returns false when finished or on error (see Err).
func (it *ResourceIterator) Next(ctx context.Context) bool {
	if it.done || it.err != nil {
		return false
	}
	if it.fetched >= it.max {
		it.err = ErrMaxPages
		return false
	}
	res, err := it.a.ListResources(ctx, it.req, it.opts...)
	if err != nil {
		it.err = err
		return false
	}
	it.fetched++
	if g := res.generation(); g != nil {
		if it.firstGen == nil {
			it.firstGen = g
		} else if *it.firstGen != *g {
			it.err = ErrGenerationChanged
			return false
		}
	}
	it.page = res
	if res.Page == nil || !res.Page.HasMore || res.Page.NextCursor == nil {
		it.done = true
	} else {
		next := *res.Page.NextCursor
		it.req.Cursor = &next
	}
	return true
}

// Page is the page fetched by the last successful Next.
func (it *ResourceIterator) Page() *ListAuthorizedResourcesResult { return it.page }

// Err is the error that ended iteration (nil after a normal end).
func (it *ResourceIterator) Err() error { return it.err }

func (r *Result[T]) generation() *int64 {
	if r.Meta == nil {
		return nil
	}
	return r.Meta.SnapshotGeneration
}
