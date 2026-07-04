# src/utils/ontology_config.py

VALID_NODE_TYPES = {
    "Material", "Property", "SynthesisMethod", "CharacterizationMethod",
    "FailureMode", "Mechanism", "Condition", "Application", "Source",
    "Equipment", "BusinessMetric", "Constraint", "InternalExperiment", "HypothesisRecord"
}

VALID_EDGE_TYPES = {
    "IMPROVES", "DEGRADES", "CAUSES", "MITIGATES", "REQUIRES_CONDITION",
    "SYNTHESIZED_BY", "CHARACTERIZED_BY", "HAS_FAILURE_MODE", "APPLIED_IN",
    "SUPPORTED_BY", "SUBCLASS_OF",
    "REQUIRES_EQUIPMENT", "USES_FEEDSTOCK", "IMPACTS_COST", "HAS_REGULATION",
    "SUBSTITUTE_FOR", "ANALOGOUS_TO",
    "TESTED_BY", "CONFIRMS", "REFUTES"
}

ONTOLOGY_CONSTRAINTS = {
    # --- Научное ядро (Causal & Structural) ---
    "IMPROVES": {"domain": {"Material", "SynthesisMethod", "Condition", "Mechanism"}, "range": {"Property"}},
    "DEGRADES": {"domain": {"Material", "Condition", "FailureMode", "Mechanism"}, "range": {"Property"}},
    "CAUSES": {"domain": {"Mechanism", "FailureMode", "Condition", "Material"}, "range": {"Property", "FailureMode"}},
    "MITIGATES": {"domain": {"Material", "SynthesisMethod", "Mechanism"}, "range": {"FailureMode"}},
    "REQUIRES_CONDITION": {"domain": {"SynthesisMethod", "Material", "CharacterizationMethod"}, "range": {"Condition"}},
    "SYNTHESIZED_BY": {"domain": {"Material"}, "range": {"SynthesisMethod"}},
    "CHARACTERIZED_BY": {"domain": {"Material", "Property"}, "range": {"CharacterizationMethod"}},
    "HAS_FAILURE_MODE": {"domain": {"Material", "Application"}, "range": {"FailureMode"}},
    "APPLIED_IN": {"domain": {"Material"}, "range": {"Application"}},
    "SUBCLASS_OF": {"domain": VALID_NODE_TYPES - {"Source", "InternalExperiment", "HypothesisRecord"}, 
                    "range": VALID_NODE_TYPES - {"Source", "InternalExperiment", "HypothesisRecord"}},
    
    # --- Индустриальный слой (Business & Constraints) ---
    "REQUIRES_EQUIPMENT": {"domain": {"SynthesisMethod", "CharacterizationMethod"}, "range": {"Equipment"}},
    "USES_FEEDSTOCK": {"domain": {"SynthesisMethod", "Material"}, "range": {"Material"}},
    "IMPACTS_COST": {"domain": {"Material", "SynthesisMethod", "Equipment"}, "range": {"BusinessMetric"}},
    "HAS_REGULATION": {"domain": {"Material", "Application", "SynthesisMethod"}, "range": {"Constraint"}},
    "SUBSTITUTE_FOR": {"domain": {"Material"}, "range": {"Material"}},
    "ANALOGOUS_TO": {"domain": {"Material", "Mechanism"}, "range": {"Material", "Mechanism"}},
    
    # --- Слой обратной связи (Feedback Loop) ---
    "TESTED_BY": {"domain": {"HypothesisRecord"}, "range": {"InternalExperiment"}},
    "CONFIRMS": {"domain": {"InternalExperiment"}, "range": {"HypothesisRecord"}},
    "REFUTES": {"domain": {"InternalExperiment"}, "range": {"HypothesisRecord"}},
    
    # --- Прослеживаемость (Provenance) ---
    "SUPPORTED_BY": {"domain": VALID_NODE_TYPES - {"Source"}, "range": {"Source"}},
}

# Роли для бустинга при формировании подграфа (Compressive KG)
CAUSAL_ROLES = {"IMPROVES", "DEGRADES", "CAUSES", "MITIGATES", "REQUIRES_CONDITION", "HAS_FAILURE_MODE"}
BUSINESS_ROLES = {"IMPACTS_COST", "HAS_REGULATION", "SUBSTITUTE_FOR", "REQUIRES_EQUIPMENT"}