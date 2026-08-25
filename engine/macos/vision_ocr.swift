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

func recognize(_ image: CGImage, frame: Int, seconds: Double) throws -> FrameResult {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["ja-JP", "en-US"]
    request.usesLanguageCorrection = true
    request.minimumTextHeight = 0.008
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    try handler.perform([request])
    let rows = (request.results ?? []).compactMap { result -> Observation? in
        guard let top = result.topCandidates(1).first else { return nil }
        let box = result.boundingBox
        return Observation(text: top.string, confidence: top.confidence,
                           x: box.origin.x, y: box.origin.y,
                           width: box.width, height: box.height)
    }.sorted { a, b in abs(a.y - b.y) > 0.01 ? a.y > b.y : a.x < b.x }
    return FrameResult(frame: frame, seconds: seconds, width: image.width,
                       height: image.height, observations: rows)
}

func imageAt(_ url: URL) -> CGImage? {
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
    return CGImageSourceCreateImageAtIndex(source, 0, nil)
}

func videoFrames(_ url: URL, interval: Double) async throws -> [FrameResult] {
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
            results.append(try recognize(image, frame: index, seconds: second))
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
    static func main() async throws {
        guard CommandLine.arguments.count >= 2 else {
            FileHandle.standardError.write(Data("usage: vision-ocr FILE [INTERVAL]\n".utf8))
            exit(2)
        }
        let url = URL(fileURLWithPath: CommandLine.arguments[1])
        let interval = CommandLine.arguments.count > 2 ? Double(CommandLine.arguments[2]) ?? 0.8 : 0.8
        let video = ["mov", "mp4", "m4v"].contains(url.pathExtension.lowercased())
        let results: [FrameResult]
        if video {
            results = try await videoFrames(url, interval: interval)
        } else if let image = imageAt(url) {
            results = [try recognize(image, frame: 0, seconds: 0)]
        } else {
            throw NSError(domain: "VisionOCR", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "画像を読み込めません: \(url.path)"])
        }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.withoutEscapingSlashes]
        FileHandle.standardOutput.write(try encoder.encode(results))
    }
}
