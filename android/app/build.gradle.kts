plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

// google-services.json is gitignored (CI writes it from a secret). Without it the build still
// compiles, e.g. for CodeQL's autobuild, but that APK can't get pushes.
if (file("google-services.json").exists()) {
    apply(plugin = "com.google.gms.google-services")
} else {
    logger.warn("android/app/google-services.json is missing; this build won't get pushes.")
}

android {
    namespace = "com.tomerady.overqueue"
    compileSdk = 35

    defaultConfig {
        // Not the namespace above, on purpose. This is the id Android installs the app under, and
        // an APK carrying a different one installs beside the old app instead of over it -- so it
        // keeps the name the app shipped with. Nothing shows it to anyone.
        applicationId = "com.tomerady.overwatchqueue"
        minSdk = 26
        targetSdk = 35
        // CI passes these from the tag and run number (see .github/workflows/android-app.yml).
        versionCode = (findProperty("versionCode") as String?)?.toInt() ?: 1
        versionName = (findProperty("versionName") as String?) ?: "0.0.0-dev"
        buildConfigField("String", "WORKER_URL", "\"https://overwatch-queue-push-relay.tomerady.workers.dev\"")
        buildConfigField("String", "RELEASE_API", "\"https://api.github.com/repos/overjump1/overwatch-queue/releases/latest\"")
    }

    buildTypes {
        debug {
            // ./gradlew installDebug -PworkerUrl=http://10.0.2.2:8787 talks to `npx wrangler dev` from the emulator.
            // CI's dev builds pass both: the dev worker, and the dev prerelease to update from.
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
