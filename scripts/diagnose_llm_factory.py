#!/usr/bin/env python3
"""
Diagnostic script: Find the exact LLMClientFactory API.
Run: python scripts/diagnose_llm_factory.py
"""
import sys
sys.path.insert(0, ".")

print("=" * 60)
print("LLMClientFactory Diagnostic")
print("=" * 60)

try:
    from src.core.llm_client import LLMClientFactory
    print(f"\n✅ LLMClientFactory imported successfully")
    print(f"   Type: {type(LLMClientFactory)}")
    
    # List ALL public methods/attributes
    public = [m for m in dir(LLMClientFactory) if not m.startswith('_')]
    print(f"\n📋 Public methods/attributes ({len(public)}):")
    for m in public:
        attr = getattr(LLMClientFactory, m)
        kind = "method" if callable(attr) else "attribute"
        print(f"   - {m} ({kind})")
    
    # Try each possible create method
    create_methods = ['create_from_settings', 'create_for_role', 'create', 
                      'get_client', 'for_role', 'get', 'build']
    print(f"\n🔍 Checking for create methods:")
    for method_name in create_methods:
        exists = hasattr(LLMClientFactory, method_name)
        print(f"   - {method_name}: {'✅ EXISTS' if exists else '❌ not found'}")
    
    # Try to get the __init__ signature if it's a class
    import inspect
    if inspect.isclass(LLMClientFactory):
        sig = inspect.signature(LLMClientFactory.__init__)
        print(f"\n📝 __init__ signature: {sig}")
    
    # Check if there's a classmethod or staticmethod for creating
    for m in public:
        attr = getattr(LLMClientFactory, m)
        if callable(attr):
            try:
                sig = inspect.signature(attr)
                print(f"\n📝 {m}{sig}")
            except (ValueError, TypeError):
                print(f"\n📝 {m}(... signature unavailable)")

except ImportError as e:
    print(f"\n❌ Could not import LLMClientFactory: {e}")
    
    # Try importing LLMClient directly
    try:
        from src.core.llm_client import LLMClient
        print(f"\n✅ LLMClient imported successfully")
        import inspect
        sig = inspect.signature(LLMClient.__init__)
        print(f"   __init__ signature: {sig}")
    except ImportError as e2:
        print(f"❌ Could not import LLMClient either: {e2}")

# Also check what's in config/settings
print("\n" + "=" * 60)
print("Settings Check")
print("=" * 60)
try:
    from src.core.config import settings
    model_attrs = [a for a in dir(settings) if 'model' in a.lower() or 'llm' in a.lower()]
    print(f"\n📋 Model-related settings:")
    for a in model_attrs:
        val = getattr(settings, a, "N/A")
        print(f"   - {a} = {val}")
except Exception as e:
    print(f"❌ Could not load settings: {e}")

print("\n" + "=" * 60)
print("DONE — Share this output so we can fix the exact method call")
print("=" * 60)
