
$('.menu').on('click', 'a', function(e) {
    let id = $(this).attr('href');

    $('.content').children().removeClass('active');
    $(id).addClass('active');

    $('.menu').children().removeClass('active');
    $(this).addClass('active');
});


if (location.href.includes('#')) {
    let id = location.href.split('#')[1];
    $("a[href='#" + id + "']").click()
}