ALLURE_RESULTS = results/allure
ALLURE_REPORT  = reports/allure

.PHONY: test report open clean

# Run all tests
test:
	pytest tests/

# Run tests for a specific service: make test-service SERVICE=my_service
test-service:
	pytest tests/$(SERVICE)/

# Open live allure report in browser
report:
	allure serve $(ALLURE_RESULTS)

# Generate static HTML report
report-html:
	allure generate $(ALLURE_RESULTS) -o $(ALLURE_REPORT) --clean
	allure open $(ALLURE_REPORT)

# Remove all generated results and reports
clean:
	rm -rf $(ALLURE_RESULTS) $(ALLURE_REPORT) results/swagger-coverage
