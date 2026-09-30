plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

// Every build is QueueFox Dev -- a separate app that installs beside the real one, with its own
// pairing, the dev worker and the dev prerelease -- except main's release, which passes -Pdev=false
// (see .github/workflows/android-app.yml). So Android Studio and the dev branch never touch the
// real app.
val dev = findProperty("dev") != "false"
// The id Android installs the app under. An APK carrying a different one installs beside the old
// app instead of over it, so this never changes again once a release has gone out.
val appId = if (dev) "com.tomerady.queuefox.dev" else "com.tomerady.queuefox"
val appName = if (dev) "QueueFox Dev" else "QueueFox"
// The launcher icon's accent: the real app's orange, or purple so the dev app stands out beside it.
val iconAccent = if (dev) "#BF5AF2" else "#FF9F0A"
// The QR code's link. Each app claims only its own, so the camera opens the right one.
val pairScheme = if (dev) "queuefox-dev" else "queuefox"
val workerUrl = if (dev) "https://queuefox-push-relay-dev.tomerady.workers.dev"
    else "https://queuefox-push-relay.tomerady.workers.dev"
val releaseApi = if (dev) "https://api.github.com/repos/overjump1/queuefox/releases/tags/dev-latest"
    else "https://api.github.com/repos/overjump1/queuefox/releases/latest"

// google-services.json is gitignored (CI writes it from a secret). Without it -- or with one that
// predates this app being added to Firebase -- the build still compiles, e.g. for CodeQL's
// autobuild, but that APK can't get pushes.
val googleServices = file("google-services.json")
if (googleServices.exists() && googleServices.readText().contains("\"$appId\"")) {
    apply(plugin = "com.google.gms.google-services")
} else {
    logger.warn("android/app/google-services.json has no Firebase app for $appId; this build won't get pushes.")
}

android {
    namespace = "com.tomerady.queuefox"
    compileSdk = 35

    defaultConfig {
        applicationId = appId
        minSdk = 26
        targetSdk = 35
        // CI passes these from the tag and run number (see .github/workflows/android-app.yml).
        versionCode = (findProperty("versionCode") as String?)?.toInt() ?: 1
        versionName = (findProperty("versionName") as String?) ?: "0.0.0-dev"
        buildConfigField("String", "WORKER_URL", "\"$workerUrl\"")
        buildConfigField("String", "RELEASE_API", "\"$releaseApi\"")
        buildConfigField("String", "APP_NAME", "\"$appName\"")
        buildConfigField("String", "PAIR_SCHEME", "\"$pairScheme\"")
        resValue("color", "ic_launcher_accent", iconAccent)
        manifestPlaceholders["appName"] = appName
        manifestPlaceholders["pairScheme"] = pairScheme
    }

    buildTypes {
        debug {
            // ./gradlew installDebug -PworkerUrl=http://10.0.2.2:8787 talks to `npx wrangler dev` from the emulator.
            (findProperty("workerUrl") as String?)?.let { buildConfigField("String", "WORKER_URL", "\"$it\"") }
            (findProperty("releaseApi") as String?)?.let { buildConfigField("String", "RELEASE_API", "\"$it\"") }
        }
        release {
            isMinifyEnabled = false
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
        resValues = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin {
    jvmToolchain(17)
}

dependencies {
    implementation(project(":shared"))

    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.material3)
    implementation(libs.compose.icons)

    implementation(platform(libs.firebase.bom))
    implementation(libs.firebase.messaging)

    implementation(libs.camerax.camera2)
    implementation(libs.camerax.lifecycle)
    implementation(libs.camerax.view)
    implementation(libs.mlkit.barcode)
}
