---
name: headless-office-automation
description: Agent-native reading, editing, and programmatic generation of Word (.docx), Excel (.xlsx), and PowerPoint (.pptx) documents without Microsoft Office.
metadata:
  domain: devops
  tags: [office, docx, xlsx, pptx, automation, headless, documents, excel]
triggers:
  - "office document automation"
  - "read docx without office"
  - "generate excel spreadsheet"
  - "edit powerpoint presentation"
  - "headless office"
  - "automate word document"
---

# Headless Office Automation Skill

## Overview
Drawn from the `OfficeCLI` framework, this skill enables AI coding agents to inspect, manipulate, and generate standard Microsoft Office file formats (Word `.docx`, Excel `.xlsx`, PowerPoint `.pptx`) completely headlessly without requiring Microsoft Office or Windows COM objects installed. Using pure Python libraries (`python-docx`, `openpyxl`, `python-pptx`), documents are parsed and mutated programmatically.

## Format Handling
1. **Spreadsheets (`.xlsx`)**: Fast cell extraction, formula evaluation, column width auto-fitting, conditional formatting.
2. **Word Documents (`.docx`)**: Heading hierarchies, paragraph styling, table extraction, tracked comment insertion.
3. **Slide Presentations (`.pptx`)**: Slide layout inheritance, shape manipulation, speaker notes extraction.
4. **Safety Invariants**: Always backup the original document before in-place modification; validate XML structure after write.
