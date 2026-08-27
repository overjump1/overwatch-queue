#!/usr/bin/env python3
"""Generates OverwatchQueue.xcodeproj from the source tree.

Re-run after adding or removing source files:  python3 tools/genproject.py
Nothing is hand-edited in the .xcodeproj, so it can always be regenerated.
"""
import hashlib
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT = os.path.join(ROOT, "OverwatchQueue.xcodeproj")

ORG = "com.tomerady"
APP_ID = f"{ORG}.OverwatchQueue"
GROUP_ID = f"group.{APP_ID}"

IOS_DEPLOY = "18.0"
WATCH_DEPLOY = "11.0"


def uid(*parts):
    """Deterministic 24-hex-char pbxproj identifier."""
    h = hashlib.md5("::".join(parts).encode()).hexdigest().upper()
    return h[:24]


def swift_files(*dirs):
    """All .swift under the given repo-relative dirs, sorted, repo-relative."""
    out = []
    for d in dirs:
        base = os.path.join(ROOT, d)
        for dirpath, _, names in os.walk(base):
            for n in sorted(names):
                if n.endswith(".swift"):
                    full = os.path.join(dirpath, n)
                    out.append(os.path.relpath(full, ROOT))
    return sorted(out)


# ---------------------------------------------------------------- target spec

SHARED = ["Shared"]

TARGETS = [
    {
        "name": "OverwatchQueue",
        "product": "OverwatchQueue.app",
        "type": "com.apple.product-type.application",
        "platform": "ios",
        "bundle_id": APP_ID,
        "sources": SHARED + ["iOS"],
        "resources": ["iOS/Assets.xcassets", "Shared/Resources"],
        "infoplist": "iOS/Info.plist",
        "entitlements": "iOS/OverwatchQueue.entitlements",
        "embed_plugins": ["QueueWidgets"],
        "embed_watch": "OverwatchQueue Watch App",
    },
    {
        "name": "QueueWidgets",
        "product": "QueueWidgets.appex",
        "type": "com.apple.product-type.app-extension",
        "platform": "ios",
        "bundle_id": f"{APP_ID}.QueueWidgets",
        "sources": SHARED + ["Widgets"],
        "resources": ["iOS/Assets.xcassets", "Shared/Resources"],
        "infoplist": "Widgets/Info.plist",
        "entitlements": "Widgets/QueueWidgets.entitlements",
    },
    {
        "name": "OverwatchQueue Watch App",
        "product": "OverwatchQueue Watch App.app",
        "type": "com.apple.product-type.application",
        "platform": "watchos",
        "bundle_id": f"{APP_ID}.watchkitapp",
        "sources": SHARED + ["Watch"],
        "resources": ["Watch/Assets.xcassets", "Shared/Resources"],
        "infoplist": "Watch/Info.plist",
        "entitlements": "Watch/Watch.entitlements",
        "embed_plugins": ["WatchQueueWidgets"],
    },
    {
        "name": "WatchQueueWidgets",
        "product": "WatchQueueWidgets.appex",
        "type": "com.apple.product-type.app-extension",
        "platform": "watchos",
        "bundle_id": f"{APP_ID}.watchkitapp.WatchQueueWidgets",
        "sources": SHARED + ["WatchWidgets"],
        "resources": ["Watch/Assets.xcassets", "Shared/Resources"],
        "infoplist": "WatchWidgets/Info.plist",
        "entitlements": "WatchWidgets/WatchQueueWidgets.entitlements",
    },
    {
        "name": "QueueKitTests",
        "product": "QueueKitTests.xctest",
        "type": "com.apple.product-type.bundle.unit-test",
        "platform": "ios",
        "bundle_id": f"{APP_ID}.QueueKitTests",
        "sources": SHARED + ["Tests"],
        "resources": ["Shared/Resources"],
        "infoplist": None,
        "test_host": "OverwatchQueue",
    },
]

BY_NAME = {t["name"]: t for t in TARGETS}


# --------------------------------------------------------------- file catalog

class Files:
    """Assigns one PBXFileReference per path and one PBXBuildFile per (path, target)."""

    def __init__(self):
        self.refs = {}      # path -> (id, kind)

    def ref(self, path, kind="sourcecode.swift"):
        if path not in self.refs:
            self.refs[path] = (uid("ref", path), kind)
        return self.refs[path][0]

    def build(self, path, target):
        return uid("build", path, target)


F = Files()

FILE_KINDS = {
    ".swift": ("sourcecode.swift", "PBXFileReference"),
    ".plist": ("text.plist.xml", "PBXFileReference"),
    ".xcassets": ("folder.assetcatalog", "PBXFileReference"),
    ".entitlements": ("text.plist.entitlements", "PBXFileReference"),
    ".json": ("text.json", "PBXFileReference"),
    ".caf": ("file", "PBXFileReference"),
    ".md": ("net.daringfireball.markdown", "PBXFileReference"),
}

PRODUCT_KINDS = {
    ".app": "wrapper.application",
    ".appex": "wrapper.app-extension",
    ".xctest": "wrapper.cfbundle",
}


def kind_for(path):
    ext = os.path.splitext(path)[1]
    return FILE_KINDS.get(ext, ("file", "PBXFileReference"))[0]


def resolve_resources(paths):
    """Resolve resource entries to concrete paths.

    An .xcassets stays a single reference (Xcode compiles it as a unit); a plain
    directory expands to the files inside it, so they land at the bundle root rather
    than nested in a folder reference.
    """
    out = []
    for p in paths:
        full = os.path.join(ROOT, p)
        if not os.path.exists(full):
            continue
        if os.path.isdir(full) and not p.endswith(".xcassets"):
            for name in sorted(os.listdir(full)):
                if not name.startswith("."):
                    out.append(os.path.join(p, name))
        else:
            out.append(p)
    return out


# ------------------------------------------------------------------- emitters

def build_settings_common():
    return {
        "ALWAYS_SEARCH_USER_PATHS": "NO",
        "CLANG_ENABLE_MODULES": "YES",
        "CLANG_ENABLE_OBJC_ARC": "YES",
        "COPY_PHASE_STRIP": "NO",
        "ENABLE_STRICT_OBJC_MSGSEND": "YES",
        "GCC_NO_COMMON_BLOCKS": "YES",
        "SWIFT_VERSION": "5.0",
        "SWIFT_EMIT_LOC_STRINGS": "YES",
        "ENABLE_USER_SCRIPT_SANDBOXING": "YES",
        "CODE_SIGN_STYLE": "Automatic",
        "IPHONEOS_DEPLOYMENT_TARGET": IOS_DEPLOY,
        "WATCHOS_DEPLOYMENT_TARGET": WATCH_DEPLOY,
        "MARKETING_VERSION": "1.0",
        "CURRENT_PROJECT_VERSION": "1",
        "SUPPORTS_MACCATALYST": "NO",
        "SUPPORTS_XR_DESIGNED_FOR_IPHONE_IPAD": "NO",
    }


def target_settings(t, debug):
    s = {
        "PRODUCT_BUNDLE_IDENTIFIER": t["bundle_id"],
        "PRODUCT_NAME": "$(TARGET_NAME)",
        "SWIFT_ACTIVE_COMPILATION_CONDITIONS": "DEBUG" if debug else "",
        "SWIFT_OPTIMIZATION_LEVEL": "-Onone" if debug else "-O",
        "ENABLE_PREVIEWS": "YES",
        "GENERATE_INFOPLIST_FILE": "NO",
    }
    if t["infoplist"]:
        s["INFOPLIST_FILE"] = t["infoplist"]
    if t.get("entitlements"):
        s["CODE_SIGN_ENTITLEMENTS"] = t["entitlements"]

    if t["platform"] == "ios":
        s["SDKROOT"] = "iphoneos"
        s["TARGETED_DEVICE_FAMILY"] = "1,2"
    else:
        s["SDKROOT"] = "watchos"
        s["TARGETED_DEVICE_FAMILY"] = "4"

    if t["type"] == "com.apple.product-type.application":
        s["ASSETCATALOG_COMPILER_APPICON_NAME"] = "AppIcon"
        s["INFOPLIST_KEY_UILaunchScreen_Generation"] = "YES"
        if t["platform"] == "ios":
            s["INFOPLIST_KEY_UISupportedInterfaceOrientations"] = "UIInterfaceOrientationPortrait"
    if t["type"] == "com.apple.product-type.app-extension":
        s["SKIP_INSTALL"] = "YES"
        s["ASSETCATALOG_COMPILER_WIDGET_BACKGROUND_COLOR_NAME"] = "WidgetBackground"
    if t.get("test_host"):
        host = BY_NAME[t["test_host"]]
        s["TEST_HOST"] = f"$(BUILT_PRODUCTS_DIR)/{host['product']}/$(BUNDLE_EXECUTABLE_FOLDER_PATH)/{host['name']}"
        s["BUNDLE_LOADER"] = "$(TEST_HOST)"
        s["GENERATE_INFOPLIST_FILE"] = "YES"
    return s


def fmt_value(v):
    if isinstance(v, list):
        inner = "".join(f"\n\t\t\t\t\t{fmt_value(x)}," for x in v)
        return "(" + inner + "\n\t\t\t\t)"
    if v == "" or any(c in str(v) for c in ' /$().,"*[]='):
        return '"%s"' % str(v).replace('"', '\\"')
    return str(v)


def fmt_settings(d, indent="\t\t\t\t"):
    lines = []
    for k in sorted(d):
        lines.append(f"{indent}{k} = {fmt_value(d[k])};")
    return "\n".join(lines)


def main():
    out = []
    w = out.append

    # Resolve every target's file list up front.
    for t in TARGETS:
        t["_sources"] = swift_files(*t["sources"])
        t["_resources"] = resolve_resources(t["resources"])
        t["_product_id"] = uid("product", t["name"])

    w("// !$*UTF8*$!")
    w("{")
    w("\tarchiveVersion = 1;")
    w("\tclasses = {\n\t};")
    w("\tobjectVersion = 56;")
    w("\tobjects = {")

    # ---- PBXBuildFile
    w("\n/* Begin PBXBuildFile section */")
    for t in TARGETS:
        for p in t["_sources"] + t["_resources"]:
            w(f'\t\t{F.build(p, t["name"])} /* {os.path.basename(p)} in {t["name"]} */ = '
              f'{{isa = PBXBuildFile; fileRef = {F.ref(p, kind_for(p))} /* {os.path.basename(p)} */; }};')
    # embedded products
    for t in TARGETS:
        for plug in t.get("embed_plugins", []):
            pt = BY_NAME[plug]
            w(f'\t\t{uid("embed", t["name"], plug)} /* {pt["product"]} in Embed */ = '
              f'{{isa = PBXBuildFile; fileRef = {pt["_product_id"]} /* {pt["product"]} */; '
              f'settings = {{ATTRIBUTES = (RemoveHeadersOnCopy, ); }}; }};')
        if t.get("embed_watch"):
            wt = BY_NAME[t["embed_watch"]]
            w(f'\t\t{uid("embed", t["name"], t["embed_watch"])} /* {wt["product"]} in Embed */ = '
              f'{{isa = PBXBuildFile; fileRef = {wt["_product_id"]} /* {wt["product"]} */; '
              f'settings = {{ATTRIBUTES = (RemoveHeadersOnCopy, ); }}; }};')
    w("/* End PBXBuildFile section */")

    # ---- PBXContainerItemProxy + PBXTargetDependency
    deps = []
    for t in TARGETS:
        for dep in list(t.get("embed_plugins", [])) + ([t["embed_watch"]] if t.get("embed_watch") else []):
            deps.append((t["name"], dep))
        if t.get("test_host"):
            deps.append((t["name"], t["test_host"]))

    w("\n/* Begin PBXContainerItemProxy section */")
    for owner, dep in deps:
        w(f'\t\t{uid("proxy", owner, dep)} /* PBXContainerItemProxy */ = {{')
        w("\t\t\tisa = PBXContainerItemProxy;")
        w(f'\t\t\tcontainerPortal = {uid("project")} /* Project object */;')
        w("\t\t\tproxyType = 1;")
        w(f'\t\t\tremoteGlobalIDString = {uid("target", dep)};')
        w(f'\t\t\tremoteInfo = "{dep}";')
        w("\t\t};")
    w("/* End PBXContainerItemProxy section */")

    w("\n/* Begin PBXTargetDependency section */")
    for owner, dep in deps:
        w(f'\t\t{uid("dep", owner, dep)} /* PBXTargetDependency */ = {{')
        w("\t\t\tisa = PBXTargetDependency;")
        w(f'\t\t\ttarget = {uid("target", dep)} /* {dep} */;')
        w(f'\t\t\ttargetProxy = {uid("proxy", owner, dep)} /* PBXContainerItemProxy */;')
        w("\t\t};")
    w("/* End PBXTargetDependency section */")

    # ---- PBXFileReference
    w("\n/* Begin PBXFileReference section */")
    seen = set()
    for t in TARGETS:
        for p in t["_sources"] + t["_resources"]:
            if p in seen:
                continue
            seen.add(p)
            rid = F.ref(p, kind_for(p))
            w(f'\t\t{rid} /* {os.path.basename(p)} */ = {{isa = PBXFileReference; '
              f'lastKnownFileType = {kind_for(p)}; name = "{os.path.basename(p)}"; '
              f'path = "{p}"; sourceTree = "<group>"; }};')
    # loose files referenced by build settings (Info.plist / entitlements)
    for t in TARGETS:
        for p in [t["infoplist"], t.get("entitlements")]:
            if p and p not in seen:
                seen.add(p)
                w(f'\t\t{F.ref(p, kind_for(p))} /* {os.path.basename(p)} */ = {{isa = PBXFileReference; '
                  f'lastKnownFileType = {kind_for(p)}; name = "{os.path.basename(p)}"; '
                  f'path = "{p}"; sourceTree = "<group>"; }};')
    for t in TARGETS:
        ext = os.path.splitext(t["product"])[1]
        w(f'\t\t{t["_product_id"]} /* {t["product"]} */ = {{isa = PBXFileReference; '
          f'explicitFileType = "{PRODUCT_KINDS[ext]}"; includeInIndex = 0; '
          f'path = "{t["product"]}"; sourceTree = BUILT_PRODUCTS_DIR; }};')
    w("/* End PBXFileReference section */")

    # ---- PBXFrameworksBuildPhase (empty; SwiftUI/WidgetKit/etc. auto-link)
    w("\n/* Begin PBXFrameworksBuildPhase section */")
    for t in TARGETS:
        w(f'\t\t{uid("frameworks", t["name"])} /* Frameworks */ = {{')
        w("\t\t\tisa = PBXFrameworksBuildPhase;")
        w("\t\t\tbuildActionMask = 2147483647;")
        w("\t\t\tfiles = (\n\t\t\t);")
        w("\t\t\trunOnlyForDeploymentPostprocessing = 0;")
        w("\t\t};")
    w("/* End PBXFrameworksBuildPhase section */")

    # ---- PBXGroup: mirror the folder layout
    dir_children = {}   # dir -> set of child paths (dirs and files)
    all_paths = set()
    for t in TARGETS:
        all_paths.update(t["_sources"])
        all_paths.update(t["_resources"])
        for p in [t["infoplist"], t.get("entitlements")]:
            if p:
                all_paths.add(p)

    for p in all_paths:
        parts = p.split(os.sep)
        for i in range(len(parts)):
            parent = os.sep.join(parts[:i]) if i else ""
            child = os.sep.join(parts[: i + 1])
            dir_children.setdefault(parent, set()).add(child)

    def group_id(path):
        return uid("group", path) if path else uid("group", "<root>")

    w("\n/* Begin PBXGroup section */")

    def emit_group(path):
        children = sorted(dir_children.get(path, []))
        name = os.path.basename(path) if path else "OverwatchQueue"
        w(f'\t\t{group_id(path)} /* {name} */ = {{')
        w("\t\t\tisa = PBXGroup;")
        w("\t\t\tchildren = (")
        for c in children:
            if c in dir_children:            # it's a directory
                w(f'\t\t\t\t{group_id(c)} /* {os.path.basename(c)} */,')
            else:
                w(f'\t\t\t\t{F.ref(c, kind_for(c))} /* {os.path.basename(c)} */,')
        if not path:
            w(f'\t\t\t\t{uid("group", "<products>")} /* Products */,')
        w("\t\t\t);")
        if path:
            w(f'\t\t\tname = "{name}";')
        w("\t\t\tsourceTree = \"<group>\";")
        w("\t\t};")
        for c in children:
            if c in dir_children:
                emit_group(c)

    emit_group("")
    w(f'\t\t{uid("group", "<products>")} /* Products */ = {{')
    w("\t\t\tisa = PBXGroup;")
    w("\t\t\tchildren = (")
    for t in TARGETS:
        w(f'\t\t\t\t{t["_product_id"]} /* {t["product"]} */,')
    w("\t\t\t);")
    w("\t\t\tname = Products;")
    w("\t\t\tsourceTree = \"<group>\";")
    w("\t\t};")
    w("/* End PBXGroup section */")

    # ---- PBXSourcesBuildPhase / PBXResourcesBuildPhase
    w("\n/* Begin PBXSourcesBuildPhase section */")
    for t in TARGETS:
        w(f'\t\t{uid("sources", t["name"])} /* Sources */ = {{')
        w("\t\t\tisa = PBXSourcesBuildPhase;")
        w("\t\t\tbuildActionMask = 2147483647;")
        w("\t\t\tfiles = (")
        for p in t["_sources"]:
            w(f'\t\t\t\t{F.build(p, t["name"])} /* {os.path.basename(p)} */,')
        w("\t\t\t);")
        w("\t\t\trunOnlyForDeploymentPostprocessing = 0;")
        w("\t\t};")
    w("/* End PBXSourcesBuildPhase section */")

    w("\n/* Begin PBXResourcesBuildPhase section */")
    for t in TARGETS:
        w(f'\t\t{uid("resources", t["name"])} /* Resources */ = {{')
        w("\t\t\tisa = PBXResourcesBuildPhase;")
        w("\t\t\tbuildActionMask = 2147483647;")
        w("\t\t\tfiles = (")
        for p in t["_resources"]:
            w(f'\t\t\t\t{F.build(p, t["name"])} /* {os.path.basename(p)} */,')
        w("\t\t\t);")
        w("\t\t\trunOnlyForDeploymentPostprocessing = 0;")
        w("\t\t};")
    w("/* End PBXResourcesBuildPhase section */")

    # ---- PBXCopyFilesBuildPhase (embed extensions / watch app)
    w("\n/* Begin PBXCopyFilesBuildPhase section */")
    for t in TARGETS:
        if t.get("embed_plugins"):
            w(f'\t\t{uid("copyplugins", t["name"])} /* Embed Foundation Extensions */ = {{')
            w("\t\t\tisa = PBXCopyFilesBuildPhase;")
            w("\t\t\tbuildActionMask = 2147483647;")
            w('\t\t\tdstPath = "";')
            w("\t\t\tdstSubfolderSpec = 13;")
            w("\t\t\tfiles = (")
            for plug in t["embed_plugins"]:
                w(f'\t\t\t\t{uid("embed", t["name"], plug)} /* {BY_NAME[plug]["product"]} */,')
            w("\t\t\t);")
            w('\t\t\tname = "Embed Foundation Extensions";')
            w("\t\t\trunOnlyForDeploymentPostprocessing = 0;")
            w("\t\t};")
        if t.get("embed_watch"):
            wt = BY_NAME[t["embed_watch"]]
            w(f'\t\t{uid("copywatch", t["name"])} /* Embed Watch Content */ = {{')
            w("\t\t\tisa = PBXCopyFilesBuildPhase;")
            w("\t\t\tbuildActionMask = 2147483647;")
            w('\t\t\tdstPath = "$(CONTENTS_FOLDER_PATH)/Watch";')
            w("\t\t\tdstSubfolderSpec = 16;")
            w("\t\t\tfiles = (")
            w(f'\t\t\t\t{uid("embed", t["name"], t["embed_watch"])} /* {wt["product"]} */,')
            w("\t\t\t);")
            w('\t\t\tname = "Embed Watch Content";')
            w("\t\t\trunOnlyForDeploymentPostprocessing = 0;")
            w("\t\t};")
    w("/* End PBXCopyFilesBuildPhase section */")

    # ---- PBXNativeTarget
    w("\n/* Begin PBXNativeTarget section */")
    for t in TARGETS:
        phases = [uid("sources", t["name"]), uid("frameworks", t["name"]), uid("resources", t["name"])]
        if t.get("embed_plugins"):
            phases.append(uid("copyplugins", t["name"]))
        if t.get("embed_watch"):
            phases.append(uid("copywatch", t["name"]))
        my_deps = [d for d in deps if d[0] == t["name"]]

        w(f'\t\t{uid("target", t["name"])} /* {t["name"]} */ = {{')
        w("\t\t\tisa = PBXNativeTarget;")
        w(f'\t\t\tbuildConfigurationList = {uid("cfglist", t["name"])} /* Build configuration list */;')
        w("\t\t\tbuildPhases = (")
        for p in phases:
            w(f"\t\t\t\t{p},")
        w("\t\t\t);")
        w("\t\t\tbuildRules = (\n\t\t\t);")
        w("\t\t\tdependencies = (")
        for owner, dep in my_deps:
            w(f'\t\t\t\t{uid("dep", owner, dep)} /* {dep} */,')
        w("\t\t\t);")
        w(f'\t\t\tname = "{t["name"]}";')
        w(f'\t\t\tproductName = "{t["name"]}";')
        w(f'\t\t\tproductReference = {t["_product_id"]} /* {t["product"]} */;')
        w(f'\t\t\tproductType = "{t["type"]}";')
        w("\t\t};")
    w("/* End PBXNativeTarget section */")

    # ---- PBXProject
    w("\n/* Begin PBXProject section */")
    w(f'\t\t{uid("project")} /* Project object */ = {{')
    w("\t\t\tisa = PBXProject;")
    w("\t\t\tattributes = {")
    w("\t\t\t\tBuildIndependentTargetsInParallel = 1;")
    w("\t\t\t\tLastSwiftUpdateCheck = 1610;")
    w("\t\t\t\tLastUpgradeCheck = 1610;")
    w("\t\t\t\tTargetAttributes = {")
    for t in TARGETS:
        w(f'\t\t\t\t\t{uid("target", t["name"])} = {{')
        w("\t\t\t\t\t\tCreatedOnToolsVersion = 16.1;")
        if t.get("test_host"):
            w(f'\t\t\t\t\t\tTestTargetID = {uid("target", t["test_host"])};')
        w("\t\t\t\t\t};")
    w("\t\t\t\t};")
    w("\t\t\t};")
    w(f'\t\t\tbuildConfigurationList = {uid("cfglist", "<project>")} /* Build configuration list */;')
    w('\t\t\tcompatibilityVersion = "Xcode 14.0";')
    w("\t\t\tdevelopmentRegion = en;")
    w("\t\t\thasScannedForEncodings = 0;")
    w("\t\t\tknownRegions = (\n\t\t\t\ten,\n\t\t\t\tBase,\n\t\t\t);")
    w(f'\t\t\tmainGroup = {group_id("")};')
    w(f'\t\t\tproductRefGroup = {uid("group", "<products>")} /* Products */;')
    w("\t\t\tprojectDirPath = \"\";")
    w("\t\t\tprojectRoot = \"\";")
    w("\t\t\ttargets = (")
    for t in TARGETS:
        w(f'\t\t\t\t{uid("target", t["name"])} /* {t["name"]} */,')
    w("\t\t\t);")
    w("\t\t};")
    w("/* End PBXProject section */")

    # ---- XCBuildConfiguration
    w("\n/* Begin XCBuildConfiguration section */")
    for cfg in ("Debug", "Release"):
        base = build_settings_common()
        if cfg == "Debug":
            base.update({
                "DEBUG_INFORMATION_FORMAT": "dwarf",
                "ENABLE_TESTABILITY": "YES",
                "GCC_OPTIMIZATION_LEVEL": "0",
                "ONLY_ACTIVE_ARCH": "YES",
                "SWIFT_OPTIMIZATION_LEVEL": "-Onone",
                "GCC_PREPROCESSOR_DEFINITIONS": ["DEBUG=1", "$(inherited)"],
            })
        else:
            base.update({
                "DEBUG_INFORMATION_FORMAT": "dwarf-with-dsym",
                "ENABLE_NS_ASSERTIONS": "NO",
                "SWIFT_COMPILATION_MODE": "wholemodule",
                "VALIDATE_PRODUCT": "YES",
            })
        w(f'\t\t{uid("cfg", "<project>", cfg)} /* {cfg} */ = {{')
        w("\t\t\tisa = XCBuildConfiguration;")
        w("\t\t\tbuildSettings = {")
        w(fmt_settings(base))
        w("\t\t\t};")
        w(f"\t\t\tname = {cfg};")
        w("\t\t};")

    for t in TARGETS:
        for cfg in ("Debug", "Release"):
            w(f'\t\t{uid("cfg", t["name"], cfg)} /* {cfg} */ = {{')
            w("\t\t\tisa = XCBuildConfiguration;")
            w("\t\t\tbuildSettings = {")
            w(fmt_settings(target_settings(t, cfg == "Debug")))
            w("\t\t\t};")
            w(f"\t\t\tname = {cfg};")
            w("\t\t};")
    w("/* End XCBuildConfiguration section */")

    # ---- XCConfigurationList
    w("\n/* Begin XCConfigurationList section */")
    for owner in ["<project>"] + [t["name"] for t in TARGETS]:
        w(f'\t\t{uid("cfglist", owner)} /* Build configuration list for {owner} */ = {{')
        w("\t\t\tisa = XCConfigurationList;")
        w("\t\t\tbuildConfigurations = (")
        w(f'\t\t\t\t{uid("cfg", owner, "Debug")} /* Debug */,')
        w(f'\t\t\t\t{uid("cfg", owner, "Release")} /* Release */,')
        w("\t\t\t);")
        w("\t\t\tdefaultConfigurationIsVisible = 0;")
        w("\t\t\tdefaultConfigurationName = Release;")
        w("\t\t};")
    w("/* End XCConfigurationList section */")

    w("\t};")
    w(f'\trootObject = {uid("project")} /* Project object */;')
    w("}")

    # ------------------------------------------------------------- write files
    if os.path.exists(PROJECT):
        shutil.rmtree(PROJECT)
    os.makedirs(os.path.join(PROJECT, "xcshareddata", "xcschemes"))
    with open(os.path.join(PROJECT, "project.pbxproj"), "w") as fh:
        fh.write("\n".join(out) + "\n")

    for t in TARGETS:
        if t["type"] == "com.apple.product-type.bundle.unit-test":
            continue
        write_scheme(t)
    print(f"Generated {PROJECT}")
    for t in TARGETS:
        print(f"  {t['name']:<28} {len(t['_sources']):>3} swift  "
              f"{len(t['_resources'])} resources")


SCHEME = """<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion = "1610" version = "1.7">
   <BuildAction parallelizeBuildables = "YES" buildImplicitDependencies = "YES">
      <BuildActionEntries>
         <BuildActionEntry buildForTesting = "YES" buildForRunning = "YES"
                           buildForProfiling = "YES" buildForArchiving = "YES"
                           buildForAnalyzing = "YES">
            <BuildableReference BuildableIdentifier = "primary"
               BlueprintIdentifier = "{tid}" BuildableName = "{product}"
               BlueprintName = "{name}" ReferencedContainer = "container:OverwatchQueue.xcodeproj">
            </BuildableReference>
         </BuildActionEntry>
      </BuildActionEntries>
   </BuildAction>
   <TestAction buildConfiguration = "Debug"
      selectedDebuggerIdentifier = "Xcode.DebuggerFoundation.Debugger.LLDB"
      selectedLauncherIdentifier = "Xcode.DebuggerFoundation.Launcher.LLDB"
      shouldUseLaunchSchemeArgsEnv = "YES">
      <Testables>{testables}</Testables>
   </TestAction>
   <LaunchAction buildConfiguration = "Debug"
      selectedDebuggerIdentifier = "Xcode.DebuggerFoundation.Debugger.LLDB"
      selectedLauncherIdentifier = "Xcode.DebuggerFoundation.Launcher.LLDB"
      launchStyle = "0" useCustomWorkingDirectory = "NO" ignoresPersistentStateOnLaunch = "NO"
      debugDocumentVersioning = "YES" debugServiceExtension = "internal"
      allowLocationSimulation = "YES">
      <BuildableProductRunnable runnableDebuggingMode = "0">
         <BuildableReference BuildableIdentifier = "primary"
            BlueprintIdentifier = "{tid}" BuildableName = "{product}"
            BlueprintName = "{name}" ReferencedContainer = "container:OverwatchQueue.xcodeproj">
         </BuildableReference>
      </BuildableProductRunnable>
   </LaunchAction>
   <ProfileAction buildConfiguration = "Release" shouldUseLaunchSchemeArgsEnv = "YES"
      savedToolIdentifier = "" useCustomWorkingDirectory = "NO" debugDocumentVersioning = "YES">
      <BuildableProductRunnable runnableDebuggingMode = "0">
         <BuildableReference BuildableIdentifier = "primary"
            BlueprintIdentifier = "{tid}" BuildableName = "{product}"
            BlueprintName = "{name}" ReferencedContainer = "container:OverwatchQueue.xcodeproj">
         </BuildableReference>
      </BuildableProductRunnable>
   </ProfileAction>
   <AnalyzeAction buildConfiguration = "Debug"></AnalyzeAction>
   <ArchiveAction buildConfiguration = "Release" revealArchiveInOrganizer = "YES"></ArchiveAction>
</Scheme>
"""

TESTABLE = """
         <TestableReference skipped = "NO">
            <BuildableReference BuildableIdentifier = "primary"
               BlueprintIdentifier = "{tid}" BuildableName = "{product}"
               BlueprintName = "{name}" ReferencedContainer = "container:OverwatchQueue.xcodeproj">
            </BuildableReference>
         </TestableReference>
      """


def write_scheme(t):
    tests = ""
    if t["name"] == "OverwatchQueue":
        tt = BY_NAME["QueueKitTests"]
        tests = TESTABLE.format(tid=uid("target", tt["name"]), product=tt["product"], name=tt["name"])
    xml = SCHEME.format(tid=uid("target", t["name"]), product=t["product"],
                        name=t["name"], testables=tests)
    path = os.path.join(PROJECT, "xcshareddata", "xcschemes", f'{t["name"]}.xcscheme')
    with open(path, "w") as fh:
        fh.write(xml)


if __name__ == "__main__":
    main()
