# Applied after cap add/sync; generated native projects remain disposable.
require 'xcodeproj'
require 'fileutils'

mobile = File.expand_path('..', __dir__)
project = Xcodeproj::Project.open(File.join(mobile, 'ios/App/App.xcodeproj'))
abort 'ReviewUITests already installed' if project.targets.any? { |t| t.name == 'ReviewUITests' }
app = project.targets.find { |t| t.name == 'App' } or abort 'App target missing'
target = project.new_target(:ui_test_bundle, 'ReviewUITests', :ios, '15.0')
target.add_dependency(app)
test_dir = File.join(mobile, 'ios/App/ReviewUITests')
FileUtils.mkdir_p(test_dir)
FileUtils.cp(File.join(mobile, 'review-ui-tests/ReviewVideoTests.swift'), test_dir)
group = project.main_group.new_group('ReviewUITests', 'ReviewUITests')
target.source_build_phase.add_file_reference(group.new_file('ReviewVideoTests.swift'))
sample = File.join(test_dir, 'app-store-review-sample.txt')
FileUtils.cp(File.join(mobile, '../docs/ops/app-store-review-sample.txt'), sample)
target.resources_build_phase.add_file_reference(group.new_file('app-store-review-sample.txt'))
target.build_configurations.each do |config|
  config.build_settings.merge!({
    'PRODUCT_BUNDLE_IDENTIFIER' => 'io.tinyassets.app.ReviewUITests',
    'SWIFT_VERSION' => '5.0',
    'GENERATE_INFOPLIST_FILE' => 'YES',
    'TEST_TARGET_NAME' => 'App',
    'TARGETED_DEVICE_FAMILY' => '1',
    'CODE_SIGNING_ALLOWED' => 'NO'
  })
end
project.save
scheme = Xcodeproj::XCScheme.new
scheme.add_build_target(app)
scheme.add_build_target(target)
scheme.add_test_target(target)
scheme.test_action.build_configuration = 'Debug'
scheme.test_action.code_coverage_enabled = false
scheme.save_as(project.path, 'ReviewVideo', true)
