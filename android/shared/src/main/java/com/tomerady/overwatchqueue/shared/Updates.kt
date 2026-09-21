package com.tomerady.overwatchqueue.shared

import android.app.PendingIntent
import android.content.Context
import android.content.pm.PackageInstaller
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/** The app's own updates, from the GitHub releases the Release workflow publishes. */
object Updates {
    private const val RELEASE_API =
        "https://api.github.com/repos/overjump1/overwatch-queue/releases/latest"
    private val ASSET = Regex("""^OWQueue-(.+)\.apk$""", RegexOption.IGNORE_CASE)
    private const val TIMEOUT_MS = 20_000

    /** The APK on offer. [version] is read from its filename. */
    data class Release(val version: String, val url: String, val size: Long)

    @Serializable
    private data class GithubRelease(val assets: List<GithubAsset> = emptyList())

    @Serializable
    private data class GithubAsset(
        val name: String = "",
        @SerialName("browser_download_url") val url: String = "",
        val size: Long = 0,
    )

    /** `v2.0.42` becomes [2, 0, 42]. A trailing non-number ends it, so `0.0.0-dev` is [0, 0, 0]. */
    fun parse(version: String?): IntArray? {
        val parts = mutableListOf<Int>()
        for (piece in version.orEmpty().trim().trimStart('v', 'V').split('.', '-', '+')) {
            parts += piece.toIntOrNull() ?: break
        }
        if (parts.isEmpty()) return null
        while (parts.size < 3) parts += 0
        return parts.take(3).toIntArray()
    }

    fun isNewer(candidate: String?, than: String?): Boolean {
        val left = parse(candidate) ?: return false
        val right = parse(than) ?: return false
        for (i in 0 until 3) {
            if (left[i] != right[i]) return left[i] > right[i]
        }
        return false
    }

    /** False for source and pull request builds: 0.x is below every release, so they'd offer one forever. */
    fun checksForUpdates(current: String?): Boolean = (parse(current)?.get(0) ?: 0) > 0

    /**
     * The APK in a `releases/latest` reply, if it's newer than [current]. The version comes from the
     * asset's filename, not the release tag: release.yml copies unchanged apps forward, so v2.0.3
     * holds OWQueue-2.0.2.apk and the tag would offer an update this build already is.
     */
    fun findUpdate(body: String, current: String?): Release? {
        val release = runCatching {
            QueueStatus.json.decodeFromString(GithubRelease.serializer(), body)
        }.getOrNull() ?: return null
        for (asset in release.assets) {
            val version = ASSET.matchEntire(asset.name)?.groupValues?.get(1) ?: continue
            return Release(version, asset.url, asset.size)
                .takeIf { asset.url.isNotEmpty() && isNewer(version, current) }
        }
        return null
    }

    /** The newer APK in the latest release, null if this build is it. Throws if GitHub can't be reached. */
    suspend fun latest(current: String?): Release? = withContext(Dispatchers.IO) {
        if (!checksForUpdates(current)) return@withContext null
        val connection = URL(RELEASE_API).openConnection() as HttpURLConnection
        val body = try {
            connection.connectTimeout = TIMEOUT_MS
            connection.readTimeout = TIMEOUT_MS
            connection.setRequestProperty("Accept", "application/vnd.github+json")
            // GitHub turns away requests without one.
            connection.setRequestProperty("User-Agent", "OWQueue/${current.orEmpty()}")
            check(connection.responseCode == 200) { "HTTP ${connection.responseCode}" }
            connection.inputStream.bufferedReader().use { it.readText() }
        } finally {
            connection.disconnect()
        }
        findUpdate(body, current)
    }

    /**
     * The APK on disk, downloaded if it isn't already there, reporting 0..100 on the way. Throws on
     * a short read: a truncated APK is one the installer rejects with nothing useful to say.
     */
    suspend fun download(
        context: Context,
        release: Release,
        onProgress: (Int) -> Unit,
    ): File = withContext(Dispatchers.IO) {
        val directory = File(context.cacheDir, "updates").apply { mkdirs() }
        val target = File(directory, "OWQueue-${release.version}.apk")
        if (release.size > 0 && target.length() == release.size) return@withContext target
        // Anything else in there is an older release's APK.
        directory.listFiles()?.forEach { it.delete() }
        val partial = File(directory, target.name + ".part")
        val connection = URL(release.url).openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = TIMEOUT_MS
            connection.readTimeout = TIMEOUT_MS
            connection.setRequestProperty("User-Agent", "OWQueue/${release.version}")
            check(connection.responseCode == 200) { "HTTP ${connection.responseCode}" }
            val total = if (release.size > 0) release.size else connection.contentLength.toLong()
            var written = 0L
            connection.inputStream.use { input ->
                partial.outputStream().use { output ->
                    val buffer = ByteArray(64 * 1024)
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        output.write(buffer, 0, read)
                        written += read
                        if (total > 0) onProgress((written * 100 / total).toInt().coerceAtMost(99))
                    }
                }
            }
            check(release.size <= 0 || written == release.size) {
                "got $written bytes, expected ${release.size}"
            }
        } catch (e: Throwable) {
            partial.delete()
            throw e
        } finally {
            connection.disconnect()
        }
        check(partial.renameTo(target)) { "couldn't move the download into place" }
        onProgress(100)
        target
    }

    /**
     * Hands [apk] to the system installer, which confirms with the user and reports on [statusTarget].
     * The default user-action policy lets Android 12+ skip that prompt once we're the installer of record.
     */
    suspend fun install(context: Context, apk: File, statusTarget: PendingIntent) =
        withContext(Dispatchers.IO) {
            val installer = context.packageManager.packageInstaller
            val params = PackageInstaller.SessionParams(
                PackageInstaller.SessionParams.MODE_FULL_INSTALL,
            ).apply { setAppPackageName(context.packageName) }
            installer.openSession(installer.createSession(params)).use { session ->
                session.openWrite("apk", 0, apk.length()).use { output ->
                    apk.inputStream().use { it.copyTo(output) }
                    session.fsync(output)
                }
                session.commit(statusTarget.intentSender)
            }
        }
}
