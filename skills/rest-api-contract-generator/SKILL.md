---
name: rest-api-contract-generator
description: Contract-first REST API specification protocol authoring strict OpenAPI 3.1 definitions, JSON schemas, typed response status codes, and mock endpoints.
metadata:
  domain: coding
  tags: [openapi, swagger, rest, api-contract, mock-server, contract-first, coding]
triggers:
  - "rest api contract generator"
  - "generate openapi spec"
  - "author rest contract"
  - "create swagger spec"
  - "api contract design"
  - "contract first development"
  - "openapi specification"
---

# REST API Contract Generator Skill

## Overview
Drawn from `spec-kit` and contract-first API development frameworks, this skill enables AI agents to author mathematically unambiguous OpenAPI 3.1 / Swagger specifications before writing backend or frontend implementations. Writing code before defining API contracts leads to breaking changes, inconsistent naming conventions, mismatched date/time formats, and unhandled 4xx/5xx edge cases. This skill establishes single-source-of-truth contracts with explicit request schemas, status code envelopes, and reusable components.

## Contract Invariants
1. **OpenAPI 3.1 Strict Conformance**: Use `openapi: 3.1.0` supporting full JSON Schema 2020-12 dialect (including `type: ["string", "null"]`).
2. **Every Endpoint Requires Responses**: Every operation must define at least a `200`/`201` success response, `400` validation error, `401`/`403` auth error, and `500` server error.
3. **Canonical Component Schemas**: Reusable entities (`User`, `ErrorResponse`, `PaginationMeta`) must live in `components.schemas` and be referenced via `$ref`.
4. **RFC 3339 Timestamps**: All timestamps must declare `type: string, format: date-time`.

## Contract Design Process
1. **Domain Boundary Mapping**: Define URL route hierarchy (`/api/v1/resources/{id}`).
2. **Schema Definition**: Author request bodies, path parameters, query filters, and payload models.
3. **Error Standardization**: Enforce RFC 7807 (Problem Details for HTTP APIs) or consistent error envelopes.
4. **Mock Server Validation**: Test contract with Prism or FastAPI mock generation to verify endpoint ergonomics.
