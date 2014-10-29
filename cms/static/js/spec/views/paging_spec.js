define([ "jquery", "js/common_helpers/ajax_helpers", "URI",
    "js/views/paging", "js/views/paging_header", "js/views/paging_footer",
    "js/models/asset", "js/collections/asset" ],
    function ($, AjaxHelpers, URI, PagingView, PagingHeader, PagingFooter, AssetModel, AssetCollection) {

        var createMockAsset = function(index) {
            var id = 'asset_' + index;
            return {
                id: id,
                display_name: id,
                url: id
            };
        };

        var mockFirstPage = {
            assets: [
                createMockAsset(1),
                createMockAsset(2),
                createMockAsset(3)
            ],
            pageSize: 3,
            totalCount: 4,
            page: 0,
            start: 0,
            end: 2
        };
        var mockSecondPage = {
            assets: [
                createMockAsset(4)
            ],
            pageSize: 3,
            totalCount: 4,
            page: 1,
            start: 3,
            end: 4
        };
        var mockEmptyPage = {
            assets: [],
            pageSize: 3,
            totalCount: 0,
            page: 0,
            start: 0,
            end: 0
        };

        var respondWithMockAssets = function(requests) {
            var requestIndex = requests.length - 1;
            var request = requests[requestIndex];
            var url = new URI(request.url);
            var queryParameters = url.query(true); // Returns an object with each query parameter stored as a value
            var page = queryParameters.page;
            var response = page === "0" ? mockFirstPage : mockSecondPage;
            AjaxHelpers.respondWithJson(requests, response, requestIndex);
        };

        var MockPagingView = PagingView.extend({
            renderPageItems: function() {},
            initialize : function() {
                this.registerSortableColumn('name-col', 'Name', 'name', 'asc');
                this.registerSortableColumn('date-col', 'Date', 'date', 'desc');
                this.setInitialSortColumn('date-col');
            }
        });

        describe("Paging", function() {
            var pagingView;

            beforeEach(function () {
                var assets = new AssetCollection();
                assets.url = "assets_url";
                var feedbackTpl = readFixtures('system-feedback.underscore');
                setFixtures($("<script>", { id: "system-feedback-tpl", type: "text/template" }).text(feedbackTpl));
                pagingView = new MockPagingView({collection: assets});
            });


            describe("PagingView", function () {
                describe("setPage", function () {
                    it('can set the current page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        expect(pagingView.collection.currentPage).toBe(0);
                        pagingView.setPage(1);
                        respondWithMockAssets(requests);
                        expect(pagingView.collection.currentPage).toBe(1);
                    });

                    it('should not change page after a server error', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        pagingView.setPage(1);
                        requests[1].respond(500);
                        expect(pagingView.collection.currentPage).toBe(0);
                    });
                });

                describe("nextPage", function () {
                    it('does not move forward after a server error', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        pagingView.nextPage();
                        requests[1].respond(500);
                        expect(pagingView.collection.currentPage).toBe(0);
                    });

                    it('can move to the next page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        pagingView.nextPage();
                        respondWithMockAssets(requests);
                        expect(pagingView.collection.currentPage).toBe(1);
                    });

                    it('can not move forward from the final page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(1);
                        respondWithMockAssets(requests);
                        pagingView.nextPage();
                        expect(requests.length).toBe(1);
                    });
                });

                describe("previousPage", function () {

                    it('can move back a page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(1);
                        respondWithMockAssets(requests);
                        pagingView.previousPage();
                        respondWithMockAssets(requests);
                        expect(pagingView.collection.currentPage).toBe(0);
                    });

                    it('can not move back from the first page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        pagingView.previousPage();
                        expect(requests.length).toBe(1);
                    });

                    it('does not move back after a server error', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(1);
                        respondWithMockAssets(requests);
                        pagingView.previousPage();
                        requests[1].respond(500);
                        expect(pagingView.collection.currentPage).toBe(1);
                    });
                });

                describe("toggleSortOrder", function () {

                    it('can toggle direction of the current sort', function () {
                        var requests = AjaxHelpers.requests(this);
                        expect(pagingView.collection.sortDirection).toBe('desc');
                        pagingView.toggleSortOrder('date-col');
                        respondWithMockAssets(requests);
                        expect(pagingView.collection.sortDirection).toBe('asc');
                        pagingView.toggleSortOrder('date-col');
                        respondWithMockAssets(requests);
                        expect(pagingView.collection.sortDirection).toBe('desc');
                    });

                    it('sets the correct default sort direction for a column', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.toggleSortOrder('name-col');
                        respondWithMockAssets(requests);
                        expect(pagingView.sortDisplayName()).toBe('Name');
                        expect(pagingView.collection.sortDirection).toBe('asc');
                        pagingView.toggleSortOrder('date-col');
                        respondWithMockAssets(requests);
                        expect(pagingView.sortDisplayName()).toBe('Date');
                        expect(pagingView.collection.sortDirection).toBe('desc');
                    });
                });

                describe("sortableColumnInfo", function () {

                    it('returns the registered info for a column', function () {
                        pagingView.registerSortableColumn('test-col', 'Test Column', 'testField', 'asc');
                        var sortInfo = pagingView.sortableColumnInfo('test-col');
                        expect(sortInfo.displayName).toBe('Test Column');
                        expect(sortInfo.fieldName).toBe('testField');
                        expect(sortInfo.defaultSortDirection).toBe('asc');
                    });

                    it('throws an exception for an unregistered column', function () {
                        expect(function() {
                            pagingView.sortableColumnInfo('no-such-column');
                        }).toThrow();
                    });
                });
            });

            describe("PagingHeader", function () {
                var pagingHeader;

                beforeEach(function () {
                    var pagingHeaderTpl = readFixtures('paging-header.underscore');
                    appendSetFixtures($("<script>", { id: "paging-header-tpl", type: "text/template" }).text(pagingHeaderTpl));
                    pagingHeader = new PagingHeader({view: pagingView});
                });

                describe("Next page button", function () {
                    beforeEach(function () {
                        // Render the page and header so that they can react to events
                        pagingView.render();
                        pagingHeader.render();
                    });

                    it('does not move forward if a server error occurs', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        pagingHeader.$('.next-page-link').click();
                        requests[1].respond(500);
                        expect(pagingView.collection.currentPage).toBe(0);
                    });

                    it('can move to the next page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        pagingHeader.$('.next-page-link').click();
                        respondWithMockAssets(requests);
                        expect(pagingView.collection.currentPage).toBe(1);
                    });

                    it('should be enabled when there is at least one more page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        expect(pagingHeader.$('.next-page-link')).not.toHaveClass('is-disabled');
                    });

                    it('should be disabled on the final page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(1);
                        respondWithMockAssets(requests);
                        expect(pagingHeader.$('.next-page-link')).toHaveClass('is-disabled');
                    });

                    it('should be disabled on an empty page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        AjaxHelpers.respondWithJson(requests, mockEmptyPage);
                        expect(pagingHeader.$('.next-page-link')).toHaveClass('is-disabled');
                    });
                });

                describe("Previous page button", function () {
                    beforeEach(function () {
                        // Render the page and header so that they can react to events
                        pagingView.render();
                        pagingHeader.render();
                    });

                    it('does not move back if a server error occurs', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(1);
                        respondWithMockAssets(requests);
                        pagingHeader.$('.previous-page-link').click();
                        requests[1].respond(500);
                        expect(pagingView.collection.currentPage).toBe(1);
                    });

                    it('can go back a page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(1);
                        respondWithMockAssets(requests);
                        pagingHeader.$('.previous-page-link').click();
                        respondWithMockAssets(requests);
                        expect(pagingView.collection.currentPage).toBe(0);
                    });

                    it('should be disabled on the first page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        expect(pagingHeader.$('.previous-page-link')).toHaveClass('is-disabled');
                    });

                    it('should be enabled on the second page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(1);
                        respondWithMockAssets(requests);
                        expect(pagingHeader.$('.previous-page-link')).not.toHaveClass('is-disabled');
                    });

                    it('should be disabled for an empty page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        AjaxHelpers.respondWithJson(requests, mockEmptyPage);
                        expect(pagingHeader.$('.previous-page-link')).toHaveClass('is-disabled');
                    });
                });

                describe("Page metadata section", function() {
                    it('shows the correct metadata for the current page', function () {
                        var requests = AjaxHelpers.requests(this),
                            message;
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        message = pagingHeader.$('.meta').html().trim();
                        expect(message).toBe('<p>Showing <span class="count-current-shown">1-3</span>' +
                            ' out of <span class="count-total">4 total</span>, ' +
                            'sorted by <span class="sort-order">Date</span> descending</p>');
                    });

                    it('shows the correct metadata when sorted ascending', function () {
                        var requests = AjaxHelpers.requests(this),
                            message;
                        pagingView.setPage(0);
                        pagingView.toggleSortOrder('name-col');
                        respondWithMockAssets(requests);
                        message = pagingHeader.$('.meta').html().trim();
                        expect(message).toBe('<p>Showing <span class="count-current-shown">1-3</span>' +
                            ' out of <span class="count-total">4 total</span>, ' +
                            'sorted by <span class="sort-order">Name</span> ascending</p>');
                    });
                });

                describe("Asset count label", function () {
                    it('should show correct count on first page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        expect(pagingHeader.$('.count-current-shown')).toHaveHtml('1-3');
                    });

                    it('should show correct count on second page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(1);
                        respondWithMockAssets(requests);
                        expect(pagingHeader.$('.count-current-shown')).toHaveHtml('4-4');
                    });

                    it('should show correct count for an empty collection', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        AjaxHelpers.respondWithJson(requests, mockEmptyPage);
                        expect(pagingHeader.$('.count-current-shown')).toHaveHtml('0-0');
                    });
                });

                describe("Asset total label", function () {
                    it('should show correct total on the first page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        expect(pagingHeader.$('.count-total')).toHaveText('4 total');
                    });

                    it('should show correct total on the second page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(1);
                        respondWithMockAssets(requests);
                        expect(pagingHeader.$('.count-total')).toHaveText('4 total');
                    });

                    it('should show zero total for an empty collection', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        AjaxHelpers.respondWithJson(requests, mockEmptyPage);
                        expect(pagingHeader.$('.count-total')).toHaveText('0 total');
                    });
                });

                describe("Sort order label", function () {
                    it('should show correct initial sort order', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        expect(pagingHeader.$('.sort-order')).toHaveText('Date');
                    });

                    it('should show updated sort order', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.toggleSortOrder('name-col');
                        respondWithMockAssets(requests);
                        expect(pagingHeader.$('.sort-order')).toHaveText('Name');
                    });
                });
            });

            describe("PagingFooter", function () {
                var pagingFooter;

                beforeEach(function () {
                    var pagingFooterTpl = readFixtures('paging-footer.underscore');
                    appendSetFixtures($("<script>", { id: "paging-footer-tpl", type: "text/template" }).text(pagingFooterTpl));
                    pagingFooter = new PagingFooter({view: pagingView});
                });

                describe("Next page button", function () {
                    beforeEach(function () {
                        // Render the page and header so that they can react to events
                        pagingView.render();
                        pagingFooter.render();
                    });

                    it('does not move forward if a server error occurs', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        pagingFooter.$('.next-page-link').click();
                        requests[1].respond(500);
                        expect(pagingView.collection.currentPage).toBe(0);
                    });

                    it('can move to the next page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        pagingFooter.$('.next-page-link').click();
                        respondWithMockAssets(requests);
                        expect(pagingView.collection.currentPage).toBe(1);
                    });

                    it('should be enabled when there is at least one more page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        expect(pagingFooter.$('.next-page-link')).not.toHaveClass('is-disabled');
                    });

                    it('should be disabled on the final page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(1);
                        respondWithMockAssets(requests);
                        expect(pagingFooter.$('.next-page-link')).toHaveClass('is-disabled');
                    });

                    it('should be disabled on an empty page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        AjaxHelpers.respondWithJson(requests, mockEmptyPage);
                        expect(pagingFooter.$('.next-page-link')).toHaveClass('is-disabled');
                    });
                });

                describe("Previous page button", function () {
                    beforeEach(function () {
                        // Render the page and header so that they can react to events
                        pagingView.render();
                        pagingFooter.render();
                    });

                    it('does not move back if a server error occurs', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(1);
                        respondWithMockAssets(requests);
                        pagingFooter.$('.previous-page-link').click();
                        requests[1].respond(500);
                        expect(pagingView.collection.currentPage).toBe(1);
                    });

                    it('can go back a page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(1);
                        respondWithMockAssets(requests);
                        pagingFooter.$('.previous-page-link').click();
                        respondWithMockAssets(requests);
                        expect(pagingView.collection.currentPage).toBe(0);
                    });

                    it('should be disabled on the first page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        expect(pagingFooter.$('.previous-page-link')).toHaveClass('is-disabled');
                    });

                    it('should be enabled on the second page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(1);
                        respondWithMockAssets(requests);
                        expect(pagingFooter.$('.previous-page-link')).not.toHaveClass('is-disabled');
                    });

                    it('should be disabled for an empty page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        AjaxHelpers.respondWithJson(requests, mockEmptyPage);
                        expect(pagingFooter.$('.previous-page-link')).toHaveClass('is-disabled');
                    });
                });

                describe("Current page label", function () {
                    it('should show 1 on the first page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        expect(pagingFooter.$('.current-page')).toHaveText('1');
                    });

                    it('should show 2 on the second page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(1);
                        respondWithMockAssets(requests);
                        expect(pagingFooter.$('.current-page')).toHaveText('2');
                    });

                    it('should show 1 for an empty collection', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        AjaxHelpers.respondWithJson(requests, mockEmptyPage);
                        expect(pagingFooter.$('.current-page')).toHaveText('1');
                    });
                });

                describe("Page total label", function () {
                    it('should show the correct value with more than one page', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        expect(pagingFooter.$('.total-pages')).toHaveText('2');
                    });

                    it('should show page 1 when there are no assets', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        AjaxHelpers.respondWithJson(requests, mockEmptyPage);
                        expect(pagingFooter.$('.total-pages')).toHaveText('1');
                    });
                });

                describe("Page input field", function () {
                    var input;

                    beforeEach(function () {
                        pagingFooter.render();
                    });

                    it('should initially have a blank page input', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        expect(pagingFooter.$('.page-number-input')).toHaveValue('');
                    });

                    it('should handle invalid page requests', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        pagingFooter.$('.page-number-input').val('abc');
                        pagingFooter.$('.page-number-input').trigger('change');
                        expect(pagingView.collection.currentPage).toBe(0);
                        expect(pagingFooter.$('.page-number-input')).toHaveValue('');
                    });

                    it('should switch pages via the input field', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        pagingFooter.$('.page-number-input').val('2');
                        pagingFooter.$('.page-number-input').trigger('change');
                        AjaxHelpers.respondWithJson(requests, mockSecondPage);
                        expect(pagingView.collection.currentPage).toBe(1);
                        expect(pagingFooter.$('.page-number-input')).toHaveValue('');
                    });

                    it('should handle AJAX failures when switching pages via the input field', function () {
                        var requests = AjaxHelpers.requests(this);
                        pagingView.setPage(0);
                        respondWithMockAssets(requests);
                        pagingFooter.$('.page-number-input').val('2');
                        pagingFooter.$('.page-number-input').trigger('change');
                        requests[1].respond(500);
                        expect(pagingView.collection.currentPage).toBe(0);
                        expect(pagingFooter.$('.page-number-input')).toHaveValue('');
                    });
                });
            });
        });
    });
