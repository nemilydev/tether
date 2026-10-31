#!/bin/bash
echo "--- who defines normalize_toys ---"
grep -rn "def normalize_toys" .
echo "--- who imports normalize_toys ---"
grep -rn "import.*normalize_toys" .
echo "--- coordinator imports from utils ---"
grep -n "from .utils import" custom_components/tether/coordinator.py
echo "--- views.py local imports ---"
grep -n "^from \." custom_components/tether/views.py
