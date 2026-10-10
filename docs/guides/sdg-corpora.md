---
title: "SDG corpora"
description: "What the Explorer keeps from the SDG OWL, SKOS, and SHACL files, which panels those files exercise, and what Aegir still needs to emit."
icon: "library"
---

`components/sdg-corpora` is the published SDG snapshot. The live Explorer on this lab holds the comprehensive OWL, the property vocabulary, both SKOS files, and one small feature overlay. The catalog OWL (`ontology/sdg-ontology.owl`) is not loaded on top of that: a second load of the same IRI replaces the nodes it shares.

The load order matters because a later import replaces a node with the same IRI. `vocabulary/domain-concepts.ttl` declares the scheme and does not give it a `skos:prefLabel`. `vocabulary/vocabulary.ttl` does. The two files share no concept IRIs, so the generated vocabulary is imported after the domain file and the scheme keeps the label `SDG column-type vocabulary (ontology-derived)`.

## What the graph holds

Measured on 2026-10-10 after loading the files below into the running server. Counts are the live graph, not the raw RDF.

| File | What landed |
| --- | --- |
| `ontology/sdg-ontology-comprehensive.owl` | 15,277 nodes, 13,103 edges. Ontology IRI `https://signals.zndx.org/sdg`. |
| `ontology/sdg-vocab.ttl` | 353 nodes, 112 edges. Same ontology IRI. This is an OWL property vocabulary, not a concept scheme. The registry name becomes `SDG property vocabulary` because this file is loaded second. |
| `vocabulary/domain-concepts.ttl` | 69 nodes, 138 edges. Hand-authored concepts, including scope notes. |
| `vocabulary/vocabulary.ttl` | 2,324 nodes, 3,352 edges. The generated column-type vocabulary. |
| `vocabulary/semantica-feature-overlay.ttl` | 1 concept and 7 edges. The edges exist because LIMS and the scheme were already loaded. |

The graph then has 17,962 nodes and 16,712 edges: 4,747 `owl:Class`, 4,118 object properties, 6,707 datatype properties, 2,392 `skos:Concept`, one concept scheme, and 13 nodes typed `entity`. The scheme card counts all 2,392 concepts. The ontology card counts 4,734 classes and 10,821 properties: a class or property is counted only when it carries the ontology URI, and a few nodes do not.

Semantic distance still uses the EmbeddingGemma archive already on disk (17,987 vectors). That archive was built before notation and scope notes were stored, so those strings are not in the current vectors. A class (`BFO_0000001`) and `LIMS` both return a 5-neighbor cosine neighborhood.

## SKOS that the panels read

The vocabulary list shows notation and definition. For the loaded scheme that is 1,066 notations and 1,076 definitions. Concept detail shows definition, scope note, editorial note, hidden label, broader, narrower, related, and the four mapping predicates.

`LIMS` keeps its domain-file definition (1,063 characters) and scope note (463 characters). Search for `Laboratory` returns that concept and the definition. Search for `Semantica feature probe` returns the overlay concept first.

The overlay concept is the specimen for fields the generator does not emit:

- hidden label `hidden probe label`
- editorial note
- notation `9.9` (on the vocabulary row; concept detail has no notation field)
- broader `LIMS`, so the vocabulary hierarchy lists it as a child and `LIMS` gains that narrower
- related `LIMS_QUALITY_TRACEABILITY`
- exact, close, broad, and narrow matches onto the four LIMS children and `LIMS`
- scheme `https://signals.zndx.org/sdg/scheme`

A link whose other end is outside the file is stored on the concept. The import adds an edge only when that node is already in the graph, so a file cannot invent a stub. A target that is still absent stays on the concept and still shows in detail, scheme membership, and hierarchy.

## SHACL

SHACL Studio generates shapes from the graph. It does not upload `shapes.ttl`. Validation of a Turtle document is `POST /api/ontology/shacl/validate`.

`ddl-comprehensive/f56a014216105664/shapes.ttl` (4,293,614 bytes) validates against the ontology IRI and conforms with zero violations. The shapes use `sh:targetClass`. The loaded graph is classes and properties, not instances, so those shapes select no focus nodes. A conformant result here does not mean the instances were checked.

`ddl-comprehensive/f56a014216105664/semantica-feature-shapes.ttl` targets the probe with `sh:targetNode`. Validate it against the scheme IRI, not the OWL ontology IRI. A concept that carries `scheme_uri` belongs to the scheme, so it is absent from the ontology data graph and both shapes fail there even though the notation is stored. Against `https://signals.zndx.org/sdg/scheme` the notation shape conforms and the example shape reports one violation: the probe has no `skos:example`. Concept detail has no example field, so that violation is the intended gap.

The data graph writes `skos:notation`, `skos:scopeNote`, `skos:editorialNote`, `skos:hiddenLabel`, and `skos:example` as literals, including when the text looks like a URL.

## What Semantica does not ingest

The OWL ingestor keeps URI-typed classes, object properties, datatype properties, and RDF properties. It keeps URI superclasses, labels, comments, and URI domains and ranges. It skips blank nodes. It does not extract:

- `owl:NamedIndividual` (the comprehensive file has on the order of 11,800)
- restriction superclasses whose parent is a blank node (on the order of 22,000 in that file)
- `owl:equivalentClass`, `owl:disjointWith`, and `owl:inverseOf`
- `owl:imports` as a separate fetch (BFO terms that are copied into the comprehensive file are present; `BFO_0000001` is in the graph with 13 subclasses and no `rdfs:comment`, so its label is the local name)

`skos:example` is not a concept-detail field. Notation is on the vocabulary summary and on the node, and it is not on `SKOSConceptDetail`.

## What Aegir should emit

These two files are specimens. They should be deleted once the generators write the same predicates. Aegir itself is not changed from this repo.

`scripts/build_skos_vocab.py` `write_ttl` writes `prefLabel`, `notation`, `altLabel`, `definition`, `broader`, and `inScheme`. It does not write `hiddenLabel`, `editorialNote`, `related`, or the mapping predicates. The CSV column `example_values` is not written as `skos:example`. Domain concepts are hand-authored and already carry scope notes; the generator gap is the column-type vocabulary. Emitting `skos:example` will not show on concept detail until Semantica grows that field.

`scripts/realize_sdg.py` writes `shapes.ttl` by running `kvasir shapes`. Those shapes use `sh:targetClass` on the realized classes. A Semantica-facing profile should use `sh:targetNode` on the SKOS concepts and TBox terms this server stores, or should emit one exemplar individual per `sh:targetClass`. Until one of those exists, the generated shapes file conforms here because it has nothing to check.
