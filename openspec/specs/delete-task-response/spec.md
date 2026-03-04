# delete-task-response Specification

## Purpose
TBD - created by archiving change delete-task-response-body. Update Purpose after archive.
## Requirements
### Requirement: Delete task returns response body

The `DELETE /api/tasks/{id}` endpoint SHALL return a JSON object describing the task that was deleted, instead of an empty `204 No Content` response.

The response body MUST include at minimum the task's `id`, `title`, and `status` fields.

#### Scenario: Successful delete returns task details

- **WHEN** a `DELETE /api/tasks/{id}` request is made for an existing task
- **THEN** the response status code SHALL be `200`
- **AND** the response body SHALL be a JSON object containing `id`, `title`, and `status` of the deleted task

#### Scenario: Delete of non-existent task returns 404

- **WHEN** a `DELETE /api/tasks/{id}` request is made for a non-existent task ID
- **THEN** the response status code SHALL be `404`
- **AND** the response body SHALL contain an error detail message

