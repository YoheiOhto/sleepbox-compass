import Foundation
import Vision
import AVFoundation
import ImageIO

struct Observation: Codable {
    let text: String
    let confidence: Float
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}
struct FrameResult: Codable {
    let frame: Int
    let seconds: Double
    let width: Int
    let height: Int
    let observations: [Observation]
}

/// Load the closed game vocabulary the caller wants Vision to prefer.
///
/// Japanese language correction otherwise rewrites unfamiliar katakana names
/// into ordinary words, so the recognized text stops matching any known name.
func loadCustomWords(_ path: String?) -> [String] {
    guard let path, let data = FileManager.default.contents(atPath: path),
          let words = try? JSONDecoder().decode([String].self, from: data) else { return [] }
    return words
}

func recognize(_ image: CGImage, frame: Int, seconds: Double,
               customWords: [String]) throws -> FrameResult {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["ja-JP", "en-US"]
    request.usesLanguageCorrection = true
    request.minimumTextHeight = 0.008
    request.customWords = customWords
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    do {
        try handler.perform([request])
    } catch {
        // macOS can reject a configured language set when the corresponding
        // text-recognition model is unavailable or being updated. Retry with
        // Vision's system-default language model instead of aborting the
        // whole import; downstream vocabulary matching still validates every
        // extracted field.
        let fallback = VNRecognizeTextRequest()
        try VNImageRequestHandler(cgImage: image, options: [:]).perform([fallback])
        return makeFrameResult(fallback, image: image, frame: frame, seconds: seconds)
    }
    var observations = makeObservations(request)
    // The compact header and pale locked subskills are the two places where a
    // full-screen request routinely joins neighbouring glyphs ("Lv.14" became
    // "Lv.141") or drops the S/M/L suffix. A second pass makes those letters
    // larger relative to the request and replaces only observations inside the
    // known UI regions; coordinates are mapped back to the full image.
    let regions = [(CGRect(x: 0, y: 0.84, width: 0.62, height: 0.14), true),
                   // Stop above the nature panel. Replacing that lower text
                   // with an uncorrected pass made valid nature names vanish.
                   (CGRect(x: 0.03, y: 0.20, width: 0.93, height: 0.51), false)]
    for (region, isHeader) in regions {
        let regional = VNRecognizeTextRequest()
        regional.recognitionLevel = .accurate
        regional.recognitionLanguages = ["ja-JP", "en-US"]
        regional.usesLanguageCorrection = false
        regional.minimumTextHeight = 0.02
        regional.customWords = customWords
        regional.regionOfInterest = region
        do {
            try VNImageRequestHandler(cgImage: image, options: [:]).perform([regional])
            let replacement = makeObservations(regional, region: region)
            let lower = replacement.map(\.text).joined(separator: " ").lowercased()
            // A partial header pass must not erase an SP or level that the
            // full-screen pass did read. Notification overlays are therefore
            // preserved as incomplete instead of manufacturing data.
            let completeHeader = lower.contains("sp") || lower.contains("rp")
            let hasLevel = lower.contains("lv") || lower.contains("レベル")
            if !replacement.isEmpty && (!isHeader || (completeHeader && hasLevel)) {
                observations.removeAll { observation in
                    let centerX = observation.x + observation.width / 2
                    let centerY = observation.y + observation.height / 2
                    return region.contains(CGPoint(x: centerX, y: centerY))
                }
                observations.append(contentsOf: replacement)
            }
        } catch {
            // The full-screen result is still valid when an optional regional
            // pass is unavailable.
        }
    }
    observations.sort { a, b in abs(a.y - b.y) > 0.01 ? a.y > b.y : a.x < b.x }
    return FrameResult(frame: frame, seconds: seconds, width: image.width,
                       height: image.height, observations: observations)
}

func makeFrameResult(_ request: VNRecognizeTextRequest, image: CGImage,
                     frame: Int, seconds: Double) -> FrameResult {
    let rows = makeObservations(request)
        .sorted { a, b in abs(a.y - b.y) > 0.01 ? a.y > b.y : a.x < b.x }
    return FrameResult(frame: frame, seconds: seconds, width: image.width,
                       height: image.height, observations: rows)
}

func makeObservations(_ request: VNRecognizeTextRequest,
                      region: CGRect? = nil) -> [Observation] {
    return (request.results ?? []).compactMap { result -> Observation? in
        guard let top = result.topCandidates(1).first else { return nil }
        let box = result.boundingBox
        let mapped = region.map { CGRect(x: $0.origin.x + box.origin.x * $0.width,
                                         y: $0.origin.y + box.origin.y * $0.height,
                                         width: box.width * $0.width,
                                         height: box.height * $0.height) } ?? box
        return Observation(text: top.string, confidence: top.confidence,
                           x: mapped.origin.x, y: mapped.origin.y,
                           width: mapped.width, height: mapped.height)
    }
}

func imageAt(_ url: URL) -> CGImage? {
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
    return CGImageSourceCreateImageAtIndex(source, 0, nil)
}

func videoFrames(_ url: URL, interval: Double, customWords: [String]) async throws -> [FrameResult] {
    let asset = AVURLAsset(url: url)
    let duration = try await asset.load(.duration).seconds
    let generator = AVAssetImageGenerator(asset: asset)
    generator.appliesPreferredTrackTransform = true
    generator.requestedTimeToleranceBefore = .zero
    generator.requestedTimeToleranceAfter = CMTime(seconds: min(interval / 3, 0.25), preferredTimescale: 600)
    var results: [FrameResult] = []
    var index = 0
    var second = 0.0
    let totalFrames = duration > 0 ? Int(duration / interval) + 1 : 1
    while second <= duration {
        do {
            let image = try generator.copyCGImage(at: CMTime(seconds: second, preferredTimescale: 600), actualTime: nil)
            results.append(try recognize(image, frame: index, seconds: second,
                                         customWords: customWords))
        } catch {
            // A damaged/transitional frame is skipped; later validation exposes missing fields.
        }
        index += 1
        // stdout carries only the final JSON blob, so progress is reported on stderr
        // as it happens, one line per sampled frame.
        FileHandle.standardError.write(Data("PROGRESS \(index) \(totalFrames) \(Int(second))\n".utf8))
        second += interval
    }
    return results
}

@main struct VisionOCR {
    static func main() async {
        do {
            try await run()
        } catch {
            FileHandle.standardError.write(Data("Vision OCR error: \(error)\n".utf8))
            exit(1)
        }
    }

    static func run() async throws {
        guard CommandLine.arguments.count >= 2 else {
            FileHandle.standardError.write(Data("usage: vision-ocr FILE [INTERVAL] [VOCABULARY]\n".utf8))
            exit(2)
        }
        let url = URL(fileURLWithPath: CommandLine.arguments[1])
        // A zero or negative sampling interval would never advance the frame
        // clock (and `duration / interval` would trap when converted to Int),
        // so an out-of-range request falls back to the smallest usable step.
        let requested = CommandLine.arguments.count > 2 ? Double(CommandLine.arguments[2]) ?? 0.8 : 0.8
        let interval = requested.isFinite && requested > 0 ? requested : 0.8
        let customWords = loadCustomWords(CommandLine.arguments.count > 3 ? CommandLine.arguments[3] : nil)
        let video = ["mov", "mp4", "m4v"].contains(url.pathExtension.lowercased())
        let results: [FrameResult]
        if video {
            results = try await videoFrames(url, interval: interval, customWords: customWords)
        } else if let image = imageAt(url) {
            results = [try recognize(image, frame: 0, seconds: 0, customWords: customWords)]
        } else {
            throw NSError(domain: "VisionOCR", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "画像を読み込めません: \(url.path)"])
        }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.withoutEscapingSlashes]
        FileHandle.standardOutput.write(try encoder.encode(results))
    }
}
