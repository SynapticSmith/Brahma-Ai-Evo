# Cookbook: OpenAPI 3.1 Contract Design & Mock Testing

## Objective
Author a strict OpenAPI 3.1 specification and spin up an instant local mock server for testing.

## Prerequisites
- Node.js (`npx @stoplight/prism-cli`) or Python `openapi-spec-validator`.

## Execution Recipe
1. **Draft Core OpenAPI 3.1 Document**:
   ```yaml
   openapi: 3.1.0
   info:
     title: Brahma Agent API
     version: 1.0.0
   paths:
     /api/v1/tasks:
       post:
         summary: Submit an agent task
         requestBody:
           required: true
           content:
             application/json:
               schema:
                 $ref: '#/components/schemas/TaskRequest'
         responses:
           '201':
             description: Created
             content:
               application/json:
                 schema:
                   $ref: '#/components/schemas/TaskResponse'
   components:
     schemas:
       TaskRequest:
         type: object
         required: [prompt]
         properties:
           prompt: { type: string }
       TaskResponse:
         type: object
         required: [id, status]
         properties:
           id: { type: string }
           status: { type: string, enum: [pending, running, done] }
   ```
2. **Validate Contract Syntax**:
   ```powershell
   npx @stoplight/prism-cli mock openapi.yaml --port 4010
   ```
3. **Verification Gate**:
   *Gate: Prism mock server starts without schema errors; returns 201 on valid POST requests.*
4. **Remediation**:
   If validation reports unresolved references, check `$ref` pointer paths against `components/schemas`.
