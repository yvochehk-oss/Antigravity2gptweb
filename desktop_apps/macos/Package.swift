// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "ChengduConstructionConsole",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(
            name: "ChengduConstructionConsole",
            targets: ["ChengduConstructionConsole"]
        )
    ],
    targets: [
        .executableTarget(
            name: "ChengduConstructionConsole",
            path: "Sources/ChengduConstructionConsole",
            linkerSettings: [
                .linkedFramework("AppKit"),
                .linkedFramework("ServiceManagement")
            ]
        ),
        .testTarget(
            name: "ChengduConstructionConsoleTests",
            dependencies: ["ChengduConstructionConsole"],
            path: "Tests/ChengduConstructionConsoleTests"
        )
    ]
)
