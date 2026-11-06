//! Authorization decision client. It only calls the server; it computes nothing itself.

use reqwest::Method;

use crate::client::{Call, Client, Response, RetryClass};
use crate::config::CallOptions;
use crate::error::{Error, Result};
use crate::models::{
    BatchCheckRequest, BatchCheckResponse, CheckRequest, CheckResponse, ExplainRequest,
    ExplainResponse, ListResourcesRequest, ResourceRef, RevisionResponse,
};

/// Decision endpoints. A `deny` is `Ok`; `NOT_READY` and friends are `Err` and are never
/// turned into a deny by this SDK.
#[derive(Debug, Clone)]
pub struct AuthorizationClient {
    client: Client,
}

const BASE: [&str; 3] = ["api", "v1", "authz"];

fn path(last: &str) -> Vec<String> {
    BASE.iter()
        .map(|s| (*s).to_owned())
        .chain([last.to_owned()])
        .collect()
}

impl AuthorizationClient {
    pub(crate) fn new(client: Client) -> Self {
        Self { client }
    }

    /// Decide one permission (read semantics: bounded retry applies).
    pub async fn check(&self, request: &CheckRequest) -> Result<Response<CheckResponse>> {
        self.check_with(request, &CallOptions::default()).await
    }

    /// [`Self::check`] with per-call options (request id, trace, ...).
    pub async fn check_with(
        &self,
        request: &CheckRequest,
        opts: &CallOptions,
    ) -> Result<Response<CheckResponse>> {
        let call = Call::new(Method::POST, path("check"))
            .class(RetryClass::Read)
            .json(request)?;
        self.client.execute(call, opts).await
    }

    /// Decide one permission over many resources.
    pub async fn batch_check(
        &self,
        request: &BatchCheckRequest,
    ) -> Result<Response<BatchCheckResponse>> {
        self.batch_check_with(request, &CallOptions::default())
            .await
    }

    /// [`Self::batch_check`] with per-call options.
    pub async fn batch_check_with(
        &self,
        request: &BatchCheckRequest,
        opts: &CallOptions,
    ) -> Result<Response<BatchCheckResponse>> {
        let call = Call::new(Method::POST, path("batch-check"))
            .class(RetryClass::Read)
            .json(request)?;
        self.client.execute(call, opts).await
    }

    /// Explain a decision.
    pub async fn explain(&self, request: &ExplainRequest) -> Result<Response<ExplainResponse>> {
        self.explain_with(request, &CallOptions::default()).await
    }

    /// [`Self::explain`] with per-call options.
    pub async fn explain_with(
        &self,
        request: &ExplainRequest,
        opts: &CallOptions,
    ) -> Result<Response<ExplainResponse>> {
        let call = Call::new(Method::POST, path("explain"))
            .class(RetryClass::Read)
            .json(request)?;
        self.client.execute(call, opts).await
    }

    /// Current database revision, published generation and status.
    pub async fn revision(&self) -> Result<Response<RevisionResponse>> {
        self.revision_with(&CallOptions::default()).await
    }

    /// [`Self::revision`] with per-call options.
    pub async fn revision_with(&self, opts: &CallOptions) -> Result<Response<RevisionResponse>> {
        self.client
            .execute(Call::new(Method::GET, path("revision")), opts)
            .await
    }

    /// One page of authorized resources. `CURSOR_STALE` is returned as an error.
    pub async fn list_resources(
        &self,
        request: &ListResourcesRequest,
    ) -> Result<Response<Vec<ResourceRef>>> {
        self.list_resources_with(request, &CallOptions::default())
            .await
    }

    /// [`Self::list_resources`] with per-call options.
    pub async fn list_resources_with(
        &self,
        request: &ListResourcesRequest,
        opts: &CallOptions,
    ) -> Result<Response<Vec<ResourceRef>>> {
        let call = Call::new(Method::POST, path("list-resources"))
            .paged()
            .class(RetryClass::Read)
            .json(request)?;
        self.client.execute(call, opts).await
    }

    /// A page-by-page iterator over `list-resources`.
    ///
    /// It never restarts on `CURSOR_STALE` (the error is returned; restarting is the caller's
    /// decision), never merges pages of different snapshot generations, stops at `max_pages`
    /// (default 1000) and applies back-pressure naturally: nothing is fetched until you call
    /// [`ListResourcesPager::next_page`]. Dropping the future cancels the in-flight request.
    #[must_use]
    pub fn list_resources_pager(&self, request: ListResourcesRequest) -> ListResourcesPager {
        ListResourcesPager {
            client: self.clone(),
            request,
            done: false,
            pages: 0,
            max_pages: 1000,
            first_generation: None,
            opts: CallOptions::default(),
        }
    }
}

/// See [`AuthorizationClient::list_resources_pager`].
#[derive(Debug)]
pub struct ListResourcesPager {
    client: AuthorizationClient,
    request: ListResourcesRequest,
    done: bool,
    pages: usize,
    max_pages: usize,
    first_generation: Option<i64>,
    opts: CallOptions,
}

impl ListResourcesPager {
    /// Cap on pages fetched (hard stop, no unbounded paging).
    #[must_use]
    pub fn max_pages(mut self, max_pages: usize) -> Self {
        self.max_pages = max_pages;
        self
    }

    /// Options reused for every page (join one trace across pages).
    #[must_use]
    pub fn options(mut self, opts: CallOptions) -> Self {
        self.opts = opts;
        self
    }

    /// Fetch the next page; `Ok(None)` after the last one.
    pub async fn next_page(&mut self) -> Result<Option<Response<Vec<ResourceRef>>>> {
        if self.done {
            return Ok(None);
        }
        if self.pages >= self.max_pages {
            return Err(Error::PageLimit {
                max_pages: self.max_pages,
            });
        }
        let response = self
            .client
            .list_resources_with(&self.request, &self.opts)
            .await?;
        self.pages += 1;
        if let Some(generation) = response.meta.and_then(|m| m.snapshot_generation) {
            match self.first_generation {
                None => self.first_generation = Some(generation),
                Some(first) if first != generation => {
                    self.done = true;
                    return Err(Error::GenerationChanged {
                        first,
                        now: generation,
                    });
                }
                Some(_) => {}
            }
        }
        match response.page.as_ref().and_then(|p| p.next_cursor.clone()) {
            Some(cursor) if response.page.as_ref().is_some_and(|p| p.has_more) => {
                self.request.cursor = Some(cursor);
            }
            _ => self.done = true,
        }
        Ok(Some(response))
    }
}
