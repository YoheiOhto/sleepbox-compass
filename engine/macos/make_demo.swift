import AppKit
import Foundation

let output = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "inbox/ocr-demo.png"
let size = NSSize(width: 1170, height: 2532)
let image = NSImage(size: size)
image.lockFocus()
NSColor(calibratedRed: 0.94, green: 0.96, blue: 0.91, alpha: 1).setFill()
NSRect(origin: .zero, size: size).fill()
let title = [NSAttributedString.Key.font: NSFont.systemFont(ofSize: 58, weight: .bold),
             .foregroundColor: NSColor(calibratedRed: 0.08, green: 0.18, blue: 0.12, alpha: 1)]
let body = [NSAttributedString.Key.font: NSFont.systemFont(ofSize: 39, weight: .medium),
            .foregroundColor: NSColor(calibratedRed: 0.08, green: 0.18, blue: 0.12, alpha: 1)]
let header = [NSAttributedString.Key.font: NSFont.systemFont(ofSize: 42, weight: .bold),
              .foregroundColor: NSColor(calibratedRed: 0.12, green: 0.42, blue: 0.28, alpha: 1)]
func draw(_ text: String, _ y: CGFloat, _ attrs: [NSAttributedString.Key: Any] = body) {
    text.draw(at: NSPoint(x: 100, y: y), withAttributes: attrs)
}
draw("フシギダネ", 2320, title)
draw("Lv. 15　　　SP 513", 2240)
draw("タイプ　くさ　　　きのみ　ドリ", 2165)
draw("食材", 2020, header)
draw("あまいミツ x2", 1940)
draw("あまいミツ x5　Lv.30", 1875)
draw("ほっこりポテト x6　Lv.60", 1810)
draw("メインスキル", 1645, header)
draw("食材ゲットS　Lv.1", 1570)
draw("サブスキル", 1400, header)
draw("Lv.10　げんき回復ボーナス", 1325)
draw("Lv.25　きのみの数S", 1255)
draw("Lv.50　食材確率アップM", 1185)
draw("Lv.75　おてつだいスピードS", 1115)
draw("Lv.100　リサーチEXPボーナス", 1045)
draw("せいかく", 850, header)
draw("おっとり", 775, title)
image.unlockFocus()
guard let tiff = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: tiff),
      let png = bitmap.representation(using: .png, properties: [:]) else { exit(1) }
try FileManager.default.createDirectory(at: URL(fileURLWithPath: output).deletingLastPathComponent(),
                                        withIntermediateDirectories: true)
try png.write(to: URL(fileURLWithPath: output))
print(output)
